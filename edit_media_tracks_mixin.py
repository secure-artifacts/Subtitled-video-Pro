import os
import subprocess
import tempfile
import threading

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QPixmap
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from core import get_ffmpeg_cmd
from preview_proxy import (
    PROXY_STATUS_FAILED,
    PROXY_STATUS_GENERATING,
    PROXY_STATUS_PENDING,
    PROXY_STATUS_READY,
    build_preview_proxy_command,
    clip_should_auto_proxy,
    prepare_clip_for_preview_proxy,
    preview_proxy_is_ready,
    preview_source_for_clip,
)
from render_timing import render_tail_padding_seconds
from ui_components import (
    get_exact_duration,
    get_video_dimensions,
    get_video_import_metadata,
    get_video_stream_duration,
)


class EditMediaTracksMixin:
    def generate_waveform(self, path, attr_name):
        if not path or not os.path.exists(path): return
        def _task():
            try:
                out = os.path.join(tempfile.gettempdir(), f"sh_wave_{attr_name}.png")
                cmd = [get_ffmpeg_cmd(), "-y", "-i", path, "-map", "0:a:0?", "-filter_complex", "showwavespic=s=2000x60:colors=#a6e3a1", "-frames:v", "1", out]
                flags = 0x08000000 if os.name == 'nt' else 0
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, timeout=10)
                if os.path.exists(out): QTimer.singleShot(0, lambda: self._apply_waveform(out, attr_name))
            except: pass
        threading.Thread(target=_task, daemon=True).start()

    def _apply_waveform(self, img_path, attr_name):
        setattr(self, attr_name, QPixmap(img_path)); self.timeline_widget.sync_from_controller()

    def _ensure_clip_import_metadata(self, clip):
        if not isinstance(clip, dict):
            return clip
        path = clip.get("path", "")
        if not path or not os.path.exists(path):
            return clip
        has_size = int(float(clip.get("width", 0) or 0)) > 0 and int(float(clip.get("height", 0) or 0)) > 0
        has_duration = float(clip.get("dur", 0.0) or 0.0) > 0
        if has_size and has_duration:
            return clip
        try:
            meta = get_video_import_metadata(path)
            if not has_duration and float(meta.get("duration", 0.0) or 0.0) > 0:
                clip["dur"] = float(meta.get("duration", 0.0) or 0.0)
            if not has_size:
                clip["width"] = int(meta.get("width", 0) or 0)
                clip["height"] = int(meta.get("height", 0) or 0)
            clip.setdefault("duration_probe", meta.get("duration_info", {}))
        except Exception:
            pass
        return clip

    def _clip_needs_preview_proxy(self, clip):
        clip = self._ensure_clip_import_metadata(clip)
        return bool(
            getattr(self, "preview_proxy_auto_generate", False)
            and isinstance(clip, dict)
            and clip_should_auto_proxy(clip)
        )

    def _should_defer_original_preview(self, clip):
        if not self._clip_needs_preview_proxy(clip):
            return False
        if preview_proxy_is_ready(clip):
            return False
        return clip.get("preview_proxy_status") in {PROXY_STATUS_PENDING, PROXY_STATUS_GENERATING}

    def _clip_dimensions_from_state(self, media_path):
        media_path = os.path.abspath(media_path or "")
        for clip in self.state.get("video_clips", []) or []:
            if os.path.abspath(clip.get("path", "") or "") != media_path:
                continue
            width = int(float(clip.get("width", 0) or 0))
            height = int(float(clip.get("height", 0) or 0))
            if width > 0 and height > 0:
                return width, height
        return get_video_dimensions(media_path)

    def _preview_media_path_for_clip(self, clip):
        if self._should_defer_original_preview(clip):
            return ""
        path = preview_source_for_clip(clip)
        if path and os.path.exists(path):
            return path
        return clip.get("path", "") if isinstance(clip, dict) else ""

    def _prime_video_preview_source(self, clip, announce=False):
        if not isinstance(clip, dict):
            return False
        self.last_video_image = None
        self._preview_scaled_pixmap_key = None
        self._preview_scaled_pixmap = None
        self._preview_frame_retry_count = 0
        path = self._preview_media_path_for_clip(clip)
        if path:
            self.player.setSource(QUrl.fromLocalFile(path))
            self.player.setLoops(QMediaPlayer.Loops.Infinite)
            return True
        if self._should_defer_original_preview(clip):
            self.player.setSource(QUrl())
            if announce and hasattr(self, "status_lbl"):
                self.status_lbl.setText("4K/高码率素材已加入；正在后台生成流畅预览代理，期间界面可继续操作。")
        return False

    def _prepare_preview_proxies_for_clips(self, clips, announce=False):
        for idx, clip in enumerate(clips or []):
            self._queue_preview_proxy_for_clip(clip, announce=announce and idx == 0)

    def _queue_preview_proxy_for_clip(self, clip, announce=False):
        if not getattr(self, "preview_proxy_auto_generate", False):
            return
        clip = self._ensure_clip_import_metadata(clip)
        if not clip_should_auto_proxy(clip):
            return
        proxy_path, fingerprint, needs_generation = prepare_clip_for_preview_proxy(clip)
        if not proxy_path or not needs_generation:
            return
        job_key = os.path.abspath(proxy_path)
        if job_key in self._preview_proxy_jobs:
            return
        self._preview_proxy_jobs.add(job_key)
        clip["preview_proxy_status"] = PROXY_STATUS_GENERATING
        clip["preview_proxy_error"] = ""
        if announce and hasattr(self, "status_lbl"):
            self.status_lbl.setText("正在后台生成流畅预览代理，生成后会自动切换预览源...")
        self.auto_save_cache()
        threading.Thread(
            target=self._generate_preview_proxy_task,
            args=(clip.get("path", ""), proxy_path, fingerprint),
            daemon=True,
        ).start()

    def _generate_preview_proxy_task(self, source_path, proxy_path, fingerprint):
        tmp_path = proxy_path + ".tmp.mp4"
        try:
            if not source_path or not os.path.exists(source_path):
                raise FileNotFoundError(source_path or "empty source video")
            os.makedirs(os.path.dirname(proxy_path), exist_ok=True)
            if not os.path.exists(proxy_path) or os.path.getsize(proxy_path) <= 1024:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                cmd = build_preview_proxy_command(get_ffmpeg_cmd(), source_path, tmp_path)
                flags = 0x08000000 if os.name == 'nt' else 0
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, check=True)
                if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) <= 1024:
                    raise RuntimeError("Preview proxy file was not created.")
                os.replace(tmp_path, proxy_path)
            QTimer.singleShot(0, lambda sp=source_path, pp=proxy_path, fp=fingerprint: self._finish_preview_proxy_job(sp, pp, fp, True, ""))
        except Exception as exc:
            error = str(exc)
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            QTimer.singleShot(0, lambda sp=source_path, pp=proxy_path, fp=fingerprint, err=error: self._finish_preview_proxy_job(sp, pp, fp, False, err))

    def _finish_preview_proxy_job(self, source_path, proxy_path, fingerprint, success, error):
        self._preview_proxy_jobs.discard(os.path.abspath(proxy_path))
        matched_active_clip = False
        for clip in self.state.get("video_clips", []) or []:
            if clip.get("path") != source_path or clip.get("preview_proxy_fingerprint") != fingerprint:
                continue
            if success:
                clip["preview_proxy_path"] = proxy_path
                clip["preview_proxy_status"] = PROXY_STATUS_READY
                clip["preview_proxy_error"] = ""
            else:
                clip["preview_proxy_status"] = PROXY_STATUS_FAILED
                clip["preview_proxy_error"] = error[:300]
            _, active_clip = self._video_clip_for_time(self.current_play_time)
            matched_active_clip = matched_active_clip or active_clip is clip
        if success:
            if hasattr(self, "status_lbl"):
                self.status_lbl.setText("流畅预览代理已生成，预览已切换到轻量素材。")
            if matched_active_clip:
                self.last_video_image = None
                self._preview_frame_retry_count = 0
                self._sync_video_playback_to_time(self.current_play_time, force_seek=True)
        elif hasattr(self, "status_lbl"):
            self.status_lbl.setText("流畅预览代理生成失败，已继续使用原素材预览。")
        self.auto_save_cache()

    def set_audio_path_from_file(self, file_path, record_history=True):
        if not file_path or not os.path.exists(file_path):
            return False
        file_path = self.cloud_import_media_if_needed(file_path)
        self.state["audio_path"] = file_path
        self.state["audio_source_in"] = 0.0
        self.btn_a.setText("✅ " + os.path.basename(file_path)[:15])
        self.audio_player.setSource(QUrl.fromLocalFile(file_path))
        a_dur = get_exact_duration(file_path)
        if a_dur > 0:
            self.state["a_trim"] = [0.0, a_dur]
        self._recalc_duration()
        self.generate_waveform(file_path, "a_wave_pixmap")
        self.update_timeline_size()
        self.auto_save_cache()
        if self.edit_mode:
            self.switch_inspector("audio")
        self.status_lbl.setText("🎵 音频素材已加入配音轨。")
        self.refresh_media_pool()
        if record_history:
            self.push_history()
        return True

    def add_video_clip_from_path(self, file_path, start_t=None):
        if not file_path or not os.path.exists(file_path):
            return False
        file_path = self.cloud_import_media_if_needed(file_path)
        try:
            media_meta = get_video_import_metadata(file_path)
            dur = float(media_meta.get("duration", 0.0) or 0.0)
            duration_info = media_meta.get("duration_info", {})
        except Exception:
            media_meta = {}
            dur, duration_info = 0.0, {}
        if dur <= 0:
            dur = get_video_stream_duration(file_path) or get_exact_duration(file_path)
        if dur <= 0:
            dur = 5.0
        try:
            video_w = int(media_meta.get("width", 0) or 0)
            video_h = int(media_meta.get("height", 0) or 0)
            if video_w <= 0 or video_h <= 0:
                video_w, video_h = get_video_dimensions(file_path)
        except Exception:
            video_w, video_h = 0, 0
        clips = self.state.get("video_clips", [])
        if start_t is None:
            start_t = clips[-1]["end"] if clips else 0.0
        else:
            start_t = max(0.0, float(start_t or 0.0))
        new_clip = {
            "path": file_path,
            "start": start_t,
            "end": start_t + dur,
            "dur": dur,
            "width": int(video_w or 0),
            "height": int(video_h or 0),
            "duration_probe": duration_info,
            "source_in": 0.0,
            "source_out": dur,
            "transition": {"type": "cut", "duration": 0.0}
        }
        clips.append(new_clip)
        clips.sort(key=lambda c: float(c.get("start", 0.0) or 0.0))
        self.state["video_clips"] = clips
        self.btn_v.setText("✅ 已导原素材")
        self.current_v_idx = clips.index(new_clip)
        self.current_selected_idx = -1
        self._queue_preview_proxy_for_clip(new_clip, announce=True)
        if len(clips) == 1 or not self.player.source().isValid():
            self._prime_video_preview_source(new_clip, announce=True)
            self.on_resolution_changed(self.res_combo.currentText())
            self.generate_waveform(file_path, "v_wave_pixmap")
            threading.Thread(target=self._gen_thumbs_cache, daemon=True).start()
        self._recalc_duration()
        self.auto_save_cache()
        if self.edit_mode:
            self.switch_inspector("video")
            self.sync_player_to_time(start_t)
        QTimer.singleShot(0, self._request_preview_video_refresh)
        QTimer.singleShot(280, self._request_preview_video_refresh)
        self.status_lbl.setText("🎞️ 视频素材已加入时间线。")
        self.refresh_media_pool()
        self.push_history()
        return True

    def load_audio(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择音频", "", "Audio Files (*.mp3 *.wav *.m4a *.aac *.flac *.ogg)")
        if file_path:
            self.set_audio_path_from_file(file_path)
        return

    def set_music_path_from_file(self, file_path):
        if not file_path or not os.path.exists(file_path):
            return False
        file_path = self.cloud_import_media_if_needed(file_path)
        self.state["music_path"] = file_path
        self.state.setdefault("music_volume", 35)
        music_dur = get_exact_duration(file_path)
        if music_dur and music_dur > 0:
            self.state["music_dur"] = float(music_dur)
        if hasattr(self, "btn_music"):
            self.btn_music.setText("✅ " + os.path.basename(file_path)[:15])
        if hasattr(self, "music_player"):
            self.music_player.setSource(QUrl.fromLocalFile(file_path))
            self.music_player.setLoops(QMediaPlayer.Loops.Infinite)
        if hasattr(self, "music_output"):
            self.music_output.setVolume(float(self.state.get("music_volume", 35) or 35) / 100.0)
        self.match_music_to_audio(show_message=False)
        self.update_timeline_size()
        self.auto_save_cache()
        self._update_workspace_status()
        self.status_lbl.setText("🎼 配乐已加入；导出时会自动循环/裁切匹配工程时长。")
        self.refresh_media_pool()
        self.push_history()
        return True

    def load_music(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择配乐", "", "Audio Files (*.mp3 *.wav *.m4a *.aac *.flac *.ogg)")
        if file_path:
            self.set_music_path_from_file(file_path)
        return

    def load_video(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择视频", "", "Video Files (*.mp4 *.mov *.webm *.mkv *.avi)")
        if file_path:
            self.add_video_clip_from_path(file_path)
        return

    def auto_fill_video(self):
        clips = self.state.get("video_clips", [])
        if not clips: return QMessageBox.warning(self, "提示", "请先导入一段视频作为底料！")
        a_path = self.state.get("audio_path", "")
        if not a_path: return QMessageBox.warning(self, "提示", "请先导入配音才能进行一键对齐！")
        a_dur = get_exact_duration(a_path)
        if a_dur <= 0: return
        compound_clip = clips[0]; compound_clip["start"] = 0.0; compound_clip["end"] = a_dur
        self.state["video_clips"] = [compound_clip]; self._recalc_duration(); self.auto_save_cache(); self.timeline_widget.sync_from_controller()
        self.refresh_media_pool()
        self.push_history()
        QMessageBox.information(self, "铺满成功", f"🚀 已将视频转换为复合片段！\n内部自动循环并紧密匹配音频时长 ({a_dur:.1f}s)。")

    def remove_last_video_clip(self):
        if not self._ensure_edit_mode("删除视频"):
            return
        clips = self.state.get("video_clips", [])
        if clips:
            clips.pop(); self.state["video_clips"] = clips
            if not clips: self.btn_v.setText("➕ 导入第一段画面 (MP4)"); self.player.stop(); self.v_wave_pixmap = None
            self._recalc_duration(); self.auto_save_cache(); self.update_timeline_size(); self.refresh_media_pool(); self.push_history()

    def remove_audio(self):
        if self.state.get("audio_path"):
            self.state["audio_path"] = ""
            self.state.pop("audio_source_in", None)
            self.btn_a.setText("🎵 导入独立配音 (可选)")
            self.audio_player.stop()
            self.a_wave_pixmap = None
            self._recalc_duration()
            self.update_timeline_size()
            self.auto_save_cache()
            self.status_lbl.setText("🗑️ 配音已清除")
            self.refresh_media_pool()
            self.push_history()

    def remove_music(self):
        if self.state.get("music_path"):
            self.state["music_path"] = ""
            self.state.pop("music_dur", None)
            self.state.pop("music_match_duration", None)
            self.state.pop("music_loop", None)
            if hasattr(self, "btn_music"):
                self.btn_music.setText("🎼 导入配乐 (可选)")
            if hasattr(self, "music_player"):
                self.music_player.stop()
                self.music_player.setSource(QUrl())
            self._recalc_duration()
            self.update_timeline_size()
            self.auto_save_cache()
            self._update_workspace_status()
            self.status_lbl.setText("配乐已清除")
            self.refresh_media_pool()
            self.push_history()

    def match_music_to_audio(self, show_message=True):
        music_path = self.state.get("music_path", "")
        if not music_path:
            return QMessageBox.warning(self, "提示", "请先导入配乐。")
        target_dur = 0.0
        a_path = self.state.get("audio_path", "")
        if a_path:
            a_trim = self.state.get("a_trim") or []
            if len(a_trim) >= 2:
                try:
                    target_dur = max(0.0, float(a_trim[1]) - float(a_trim[0]))
                except Exception:
                    target_dur = 0.0
            if target_dur <= 0:
                target_dur = get_exact_duration(a_path) or 0.0
        if target_dur <= 0:
            self._recalc_duration()
            target_dur = float(self.state.get("duration", 0.0) or 0.0)
        if target_dur <= 1.0:
            target_dur = float(self.state.get("music_dur", 0.0) or 0.0) or get_exact_duration(music_path) or target_dur
        self.state["music_match_duration"] = max(1.0, target_dur)
        self.state["music_loop"] = True
        self.update_timeline_size()
        self.auto_save_cache()
        self.status_lbl.setText(f"配乐已匹配到 {self.state['music_match_duration']:.1f}s，导出会自动循环/裁切。")
        if show_message:
            QMessageBox.information(self, "配乐匹配完成", f"配乐会在导出时自动循环或裁切到 {self.state['music_match_duration']:.1f} 秒。")

    def _recalc_duration(self):
        clips = self.state.get("video_clips", [])
        durations = [float(c.get("end", 0.0) or 0.0) for c in clips]

        a_path = self.state.get("audio_path")
        if a_path:
            a_trim = self.state.get("a_trim") or []
            if len(a_trim) >= 2:
                try:
                    durations.append(max(0.0, float(a_trim[1])))
                except Exception:
                    pass
            else:
                durations.append(float(get_exact_duration(a_path) or 0.0))

        subs = self.state.get("subs_data", []) or []
        durations.extend(float(s.get("end", 0.0) or 0.0) for s in subs)

        if self.state.get("music_path"):
            music_target = float(self.state.get("music_match_duration", 0.0) or 0.0)
            if music_target <= 0:
                music_target = float(self.state.get("music_dur", 0.0) or 0.0)
            if music_target <= 0:
                music_target = get_exact_duration(self.state.get("music_path")) or 0.0
            if music_target > 0:
                durations.append(music_target)

        content_dur = max(durations) if durations else 0.0
        self.state["content_duration"] = max(0.0, content_dur)
        render_dur = content_dur + render_tail_padding_seconds() if content_dur > 0 else 1.0
        self.state["duration"] = max(1.0, render_dur); self.update_timeline_size()

    def _content_duration(self):
        durations = []
        for clip in self.state.get("video_clips", []) or []:
            durations.append(float(clip.get("end", 0.0) or 0.0))

        a_path = self.state.get("audio_path", "")
        if a_path:
            a_trim = self.state.get("a_trim") or []
            if len(a_trim) >= 2:
                try:
                    durations.append(max(0.0, float(a_trim[1])))
                except Exception:
                    pass
            else:
                durations.append(float(get_exact_duration(a_path) or 0.0))

        durations.extend(float(s.get("end", 0.0) or 0.0) for s in self.state.get("subs_data", []) or [])

        if self.state.get("music_path"):
            music_target = float(self.state.get("music_match_duration", 0.0) or 0.0)
            if music_target <= 0:
                music_target = float(self.state.get("music_dur", 0.0) or 0.0)
            if music_target > 0:
                durations.append(music_target)

        content_dur = max(durations) if durations else float(self.state.get("content_duration", 0.0) or 0.0)
        return max(0.0, content_dur)
