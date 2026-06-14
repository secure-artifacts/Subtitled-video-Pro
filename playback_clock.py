import time


END_EPSILON_SECONDS = 0.02


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def clamp_time(time_sec, duration):
    duration = max(0.0, safe_float(duration, 0.0))
    return max(0.0, min(duration, safe_float(time_sec, 0.0)))


def seek_relative_time(current_time, delta, duration):
    return clamp_time(safe_float(current_time, 0.0) + safe_float(delta, 0.0), duration)


def should_reset_on_play(current_time, duration, threshold=0.03):
    duration = max(0.0, safe_float(duration, 0.0))
    return duration > 0 and safe_float(current_time, 0.0) >= duration - max(0.0, safe_float(threshold, 0.0))


class PlaybackClock:
    def __init__(self, now_func=None):
        self.now_func = now_func or time.monotonic
        self.current_time = 0.0
        self.ref_time = 0.0
        self.ref_clock = self.now_func()

    def start(self, current_time, duration):
        current_time = clamp_time(current_time, duration)
        if should_reset_on_play(current_time, duration):
            current_time = 0.0
        self.anchor(current_time)
        return current_time

    def anchor(self, current_time):
        self.current_time = max(0.0, safe_float(current_time, 0.0))
        self.ref_time = self.current_time
        self.ref_clock = self.now_func()
        return self.current_time

    def seek(self, time_sec, duration):
        return self.anchor(clamp_time(time_sec, duration))

    def seek_relative(self, delta, duration):
        return self.seek(seek_relative_time(self.current_time, delta, duration), duration)

    def tick(self, duration, loop_enabled=False, epsilon=END_EPSILON_SECONDS):
        duration = max(0.001, safe_float(duration, 0.0))
        elapsed = max(0.0, self.now_func() - self.ref_clock)
        next_time = self.ref_time + elapsed
        if next_time >= duration - max(0.0, safe_float(epsilon, 0.0)):
            if loop_enabled:
                self.anchor(0.0)
                return "loop", 0.0
            self.anchor(duration)
            return "ended", duration
        self.current_time = next_time
        return "playing", next_time
