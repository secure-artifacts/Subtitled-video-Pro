import time


class CooperativeJobControl:
    def __init__(self, run_kind=""):
        self.run_kind = run_kind
        self.pause_requested = False
        self.cancel_requested = False
        self.finish_reason = "completed"

    def reset(self, run_kind=""):
        self.run_kind = run_kind
        self.pause_requested = False
        self.cancel_requested = False
        self.finish_reason = "completed"

    def toggle_pause(self):
        if self.cancel_requested:
            return self.pause_requested
        self.pause_requested = not self.pause_requested
        return self.pause_requested

    def request_cancel(self):
        if self.cancel_requested:
            return False
        self.cancel_requested = True
        self.pause_requested = False
        self.finish_reason = "cancelled"
        return True

    def mark_cancelled(self):
        self.finish_reason = "cancelled"

    def clear_requests(self):
        self.pause_requested = False
        self.cancel_requested = False

    def wait_if_paused(self, on_pause_once=None, sleep_seconds=0.2):
        announced = False
        while self.pause_requested and not self.cancel_requested:
            if on_pause_once and not announced:
                on_pause_once()
                announced = True
            time.sleep(max(0.05, float(sleep_seconds or 0.2)))
        return not self.cancel_requested

    def state_text(self, idle, running, paused, canceling, cancelled=None, active=False):
        if self.cancel_requested:
            return canceling
        if self.pause_requested:
            return paused
        if active:
            return running
        if self.finish_reason == "cancelled" and cancelled:
            return cancelled
        return idle
