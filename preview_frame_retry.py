DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY_MS = 260


class PreviewFrameRetryPolicy:
    def __init__(self, max_retries=DEFAULT_MAX_RETRIES, retry_delay_ms=DEFAULT_RETRY_DELAY_MS):
        self.max_retries = int(max(0, max_retries))
        self.retry_delay_ms = int(max(0, retry_delay_ms))
        self.pending = False
        self.count = 0

    def reset(self):
        self.pending = False
        self.count = 0

    def mark_frame_ready(self):
        self.reset()

    def reset_for_source_change(self):
        self.pending = False
        self.count = 0

    def request_retry(self, has_video_clips, has_frame):
        if has_frame or not has_video_clips or self.pending or self.count >= self.max_retries:
            return False
        self.pending = True
        self.count += 1
        return True

    def mark_retry_window_elapsed(self):
        self.pending = False
