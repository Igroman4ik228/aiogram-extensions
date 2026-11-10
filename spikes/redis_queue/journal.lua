-- Application state/outbox only. Taskiq remains the stream transport and worker.
local arg = cjson.decode(ARGV[1])
local expected = {'hash', 'string', 'zset', 'stream', 'zset'}
for index, key in ipairs(KEYS) do
    local kind = redis.call('TYPE', key).ok
    if kind ~= 'none' and kind ~= expected[index] then
        return redis.error_reply('Invalid journal key type')
    end
end
local job, lease, outbox, dlq, expiry = unpack(KEYS)
-- Runtime script errors do not roll back earlier writes. Require the journal's
-- complete command set before making any mutation, including under restricted ACLs.
local required = {
    {'HGET', job, 'status'}, {'HGETALL', job}, {'HSET', job, 'status', 'check'},
    {'HDEL', job, 'dead_entry'}, {'PERSIST', job}, {'PEXPIRE', job, 1},
    {'GET', lease}, {'SET', lease, 'check', 'NX', 'PX', 1}, {'DEL', lease},
    {'ZADD', outbox, 0, 'check'}, {'ZREM', outbox, 'check'},
    {'XADD', dlq, '*', 'check', 'check'}, {'XDEL', dlq, '0-0'}, {'XLEN', dlq},
    {'ZADD', expiry, 0, 'check'}, {'ZREM', expiry, 'check'}, {'ZCARD', expiry},
    {'ZRANGEBYSCORE', expiry, '-inf', 0, 'LIMIT', 0, 100}, {'DEL', dlq, expiry}, {'TIME'},
}
for _, command in ipairs(required) do
    if not redis.acl_check_cmd(unpack(command)) then
        return redis.error_reply('Insufficient journal permissions')
    end
end
local time = redis.call('TIME')
local now = tonumber(time[1]) * 1000 + math.floor(tonumber(time[2]) / 1000)

if arg.op == 'cleanup' then
    local entries = redis.call('ZRANGEBYSCORE', expiry, '-inf', now, 'LIMIT', 0, 100)
    for _, entry in ipairs(entries) do
        redis.call('XDEL', dlq, entry)
        redis.call('ZREM', expiry, entry)
    end
    if redis.call('ZCARD', expiry) == 0 and redis.call('XLEN', dlq) == 0 then
        redis.call('DEL', dlq, expiry)
    end
    return #entries
end

if arg.op == 'enqueue' then
    local wire = redis.call('HGET', job, 'wire')
    if wire then
        if wire ~= arg.wire then
            return redis.error_reply('Job payload conflict')
        end
        return {0, redis.call('HGET', job, 'generation')}
    end
    redis.call('HSET', job, 'wire', arg.wire, 'generation', arg.generation,
        'status', 'queued', 'attempts', 0, 'next_at', 0)
    redis.call('ZADD', outbox, 0, arg.job .. ':' .. arg.generation)
    return {1, arg.generation}
end
if arg.op == 'replay' then
    local previous = redis.call('HGET', job, 'generation')
    if redis.call('HGET', job, 'last_replay') == arg.entry then return {0, previous} end
    if redis.call('HGET', job, 'status') ~= 'dead'
        or redis.call('HGET', job, 'dead_entry') ~= arg.entry then
        return redis.error_reply('Dead letter is not replayable')
    end
    redis.call('HSET', job, 'status', 'queued', 'attempts', 0, 'next_at', 0,
        'generation', arg.generation, 'last_replay', arg.entry, 'error', '')
    redis.call('HDEL', job, 'dead_entry')
    redis.call('PERSIST', job)
    redis.call('ZREM', outbox, arg.job .. ':' .. previous)
    redis.call('ZADD', outbox, 0, arg.job .. ':' .. arg.generation)
    redis.call('XDEL', dlq, arg.entry)
    redis.call('ZREM', expiry, arg.entry)
    return {1, arg.generation}
end
if arg.op == 'release' then
    if redis.call('GET', lease) ~= arg.owner then return 0 end
    return redis.call('DEL', lease)
end
if redis.call('HGET', job, 'generation') ~= arg.generation then
    if arg.op == 'claim' then return 'stale' end
    return redis.error_reply('Delivery generation changed')
end
local status = redis.call('HGET', job, 'status')
if arg.op == 'dead' and status == 'dead' then
    return redis.call('HGET', job, 'dead_entry')
end
if arg.op == 'claim' then
    if status == 'succeeded' or status == 'dead' then return 'terminal' end
    if redis.call('SET', lease, arg.owner, 'NX', 'PX', arg.lease_ms) then
        return 'acquired'
    end
    return 'busy'
end
if redis.call('GET', lease) ~= arg.owner then
    return redis.error_reply('Delivery ownership lost')
end
if arg.op == 'renew' then return redis.call('PEXPIRE', lease, arg.lease_ms) end
if arg.op == 'attempt' then
    if status == 'failed' then return -1 end
    if tonumber(redis.call('HGET', job, 'next_at')) > now then return 0 end
    local attempts = tonumber(redis.call('HGET', job, 'attempts'))
    if attempts >= arg.maximum then
        redis.call('HSET', job, 'status', 'failed', 'error', 'AttemptBudgetExhausted')
        return -1
    end
    redis.call('HSET', job, 'status', 'processing', 'attempts', attempts + 1, 'next_at', 0)
    return attempts + 1
end
if arg.op == 'fail' then
    if status ~= 'processing' then return redis.error_reply('Job is not processing') end
    local attempts = tonumber(redis.call('HGET', job, 'attempts'))
    if arg.retry_ms ~= cjson.null and attempts < arg.maximum then
        redis.call('HSET', job, 'status', 'retry', 'error', arg.error,
            'next_at', now + arg.retry_ms)
    else
        redis.call('HSET', job, 'status', 'failed', 'error', arg.error, 'next_at', 0)
    end
    return 1
end
if arg.op == 'succeed' then
    if status ~= 'processing' then return redis.error_reply('Job is not processing') end
    redis.call('HSET', job, 'status', 'succeeded', 'error', '', 'next_at', 0)
    redis.call('PEXPIRE', job, arg.retention_ms)
    return 1
end
if arg.op == 'dead' then
    if status ~= 'failed' then return redis.error_reply('Job is not failed') end
    local entry = redis.call('XADD', dlq, '*', 'job', arg.job, 'generation', arg.generation,
        'wire', redis.call('HGET', job, 'wire'), 'attempts', redis.call('HGET', job, 'attempts'),
        'error', redis.call('HGET', job, 'error'))
    redis.call('ZADD', expiry, now + arg.retention_ms, entry)
    redis.call('HSET', job, 'status', 'dead', 'dead_entry', entry)
    redis.call('PEXPIRE', job, arg.retention_ms)
    return entry
end
return redis.error_reply('Unknown journal operation')
