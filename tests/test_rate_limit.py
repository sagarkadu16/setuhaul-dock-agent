from setuhaul.services.rate_limit import RateLimiter


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_allows_up_to_the_limit_then_blocks():
    clock = FakeClock()
    rl = RateLimiter(limit=3, window_s=60, clock=clock)
    assert [rl.hit("DRV006") for _ in range(3)] == [0, 0, 0]
    assert rl.hit("DRV006") == 60                  # blocked: wait for the oldest hit to expire


def test_window_slides():
    clock = FakeClock()
    rl = RateLimiter(limit=2, window_s=60, clock=clock)
    rl.hit("DRV006")
    clock.now += 30
    rl.hit("DRV006")
    clock.now += 20                                # t=50: both hits still inside the window
    assert rl.hit("DRV006") == 10                  # first hit (t=0) expires at t=60
    clock.now += 10                                # t=60: first hit expired
    assert rl.hit("DRV006") == 0


def test_blocked_attempts_do_not_extend_the_wait():
    clock = FakeClock()
    rl = RateLimiter(limit=1, window_s=60, clock=clock)
    rl.hit("DRV006")
    for _ in range(100):
        rl.hit("DRV006")                           # hammering while blocked...
    clock.now += 60
    assert rl.hit("DRV006") == 0                   # ...doesn't push the unlock time back


def test_keys_are_independent():
    rl = RateLimiter(limit=1, window_s=60, clock=FakeClock())
    assert rl.hit("DRV006") == 0
    assert rl.hit("DRV012") == 0                   # another driver isn't affected