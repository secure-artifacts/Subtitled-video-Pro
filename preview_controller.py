from timeline_model import (
    clip_for_time,
    content_duration_for_state,
    playback_duration_for_state,
    video_local_time,
)


class PreviewController:
    def __init__(self, state, exact_duration=None):
        self.state = state
        self.exact_duration = exact_duration

    def content_duration(self):
        return content_duration_for_state(self.state, exact_duration=self.exact_duration)

    def playback_duration(self):
        return playback_duration_for_state(self.state, exact_duration=self.exact_duration)

    def clip_for_time(self, time_sec):
        return clip_for_time((self.state or {}).get("video_clips", []), time_sec)

    def video_local_time(self, clip, time_sec):
        return video_local_time(clip, time_sec)
