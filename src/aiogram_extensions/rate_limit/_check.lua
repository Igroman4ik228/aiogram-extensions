local has_cooldown = tonumber(ARGV[1])
local request_id = ARGV[2]
local first_rule = 1 + has_cooldown
local delays = {0}

-- Only sliding windows need a timestamp. Fixed windows and cooldowns use TTLs.
local now
if request_id ~= '' then
    local time = redis.call('TIME')
    now = tonumber(time[1]) * 1000 + math.floor(tonumber(time[2]) / 1000)
end

if has_cooldown == 1 then
    local ttl = redis.call('PTTL', KEYS[1])
    if ttl >= 0 then
        delays[1] = math.max(1, ttl)
    elseif ttl == -1 then
        return redis.error_reply('Invalid cooldown state')
    end
end

local blocked = delays[1] > 0
local counts = {}

-- Read every rule before charging any. Expired sliding entries do not count.
for i = first_rule, #KEYS do
    local arg = 2 + (i - first_rule) * 3
    local sliding = ARGV[arg + 1] == 'sliding_window'
    local window = tonumber(ARGV[arg + 2])
    local limit = tonumber(ARGV[arg + 3])
    local delay = 0

    if sliding then
        local cutoff = '(' .. string.format('%.0f', now - window)
        if redis.call('ZCOUNT', KEYS[i], cutoff, '+inf') >= limit then
            local oldest = redis.call('ZRANGEBYSCORE', KEYS[i], cutoff, '+inf',
                                      'WITHSCORES', 'LIMIT', 0, 1)
            delay = tonumber(oldest[2]) + window - now
        end
    else
        local count = redis.call('GET', KEYS[i])
        if count then
            local ttl = redis.call('PTTL', KEYS[i])
            if not string.match(count, '^[1-9]%d*$') or tonumber(count) >= 2^53 or ttl < 0 then
                return redis.error_reply('Invalid fixed-window state')
            end

            counts[i] = tonumber(count)
            if counts[i] >= limit then
                delay = math.max(1, ttl)
            end
        end
    end

    delays[#delays + 1] = delay
    blocked = blocked or delay > 0
end

if not blocked then
    local writes = {}

    local function plan_write(...)
        -- A Lua error does not roll back earlier writes.
        if not redis.acl_check_cmd(...) then
            error('Rate limit write not permitted')
        end

        writes[#writes + 1] = {...}
    end

    for i = first_rule, #KEYS do
        local arg = 2 + (i - first_rule) * 3
        local duration = ARGV[arg + 2]
        if ARGV[arg + 1] == 'sliding_window' then
            plan_write('ZREMRANGEBYSCORE', KEYS[i], '-inf',
                       string.format('%.0f', now - tonumber(duration)))
            plan_write('ZADD', KEYS[i], string.format('%.0f', now), request_id)
            plan_write('PEXPIRE', KEYS[i], duration)
        elseif counts[i] then
            plan_write('INCR', KEYS[i])
        else
            plan_write('SET', KEYS[i], 1, 'PX', duration)
        end
    end

    for _, command in ipairs(writes) do
        redis.call(unpack(command))
    end
end

return delays
