def _call(obj, method_name, *args):
    if obj is None:
        return False
    method = getattr(obj, method_name, None)
    if not callable(method):
        return False
    method(*args)
    return True


def stop_timer(timer):
    return _call(timer, "stop")


class PlayerGroupController:
    def __init__(self, video_player=None, audio_player=None, music_player=None):
        self.video_player = video_player
        self.audio_player = audio_player
        self.music_player = music_player

    def play_video(self):
        return _call(self.video_player, "play")

    def pause_video(self):
        return _call(self.video_player, "pause")

    def play_audio(self):
        return _call(self.audio_player, "play")

    def pause_audio(self):
        return _call(self.audio_player, "pause")

    def play_music(self):
        return _call(self.music_player, "play")

    def pause_music(self):
        return _call(self.music_player, "pause")

    def set_audio_position(self, position_ms):
        return _call(self.audio_player, "setPosition", int(position_ms))

    def play(self, has_audio=False, has_music=False):
        self.play_video()
        if has_audio:
            self.play_audio()
        if has_music:
            self.play_music()

    def pause(self, has_audio=False, has_music=False):
        self.pause_video()
        if has_audio:
            self.pause_audio()
        if has_music:
            self.pause_music()
