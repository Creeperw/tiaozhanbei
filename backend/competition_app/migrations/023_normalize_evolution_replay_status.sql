UPDATE evolution_rules
SET status = 'safety_replay_passed',
    updated_at = CURRENT_TIMESTAMP
WHERE status = 'replay_passed';