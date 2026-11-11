if redis.call('PTTL', KEYS[1]) < tonumber(ARGV[1]) then
    redis.call('SET', KEYS[1], 1, 'PX', ARGV[1])
end

return 1
