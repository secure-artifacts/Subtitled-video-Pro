import os

from PyQt6.QtWidgets import QFileDialog, QListWidgetItem, QMessageBox

from ui_components import get_exact_duration, get_video_import_metadata, get_video_stream_duration


class EditMediaMixin:
    def _ensure_edit_mode(self, action="剪辑"):
        self.edit_mode = True
        return True

    def _supported_media_path(self, file_path):
        ext = os.path.splitext(file_path or "")[1].lower()
        if ext in self._video_exts:
            return "video"
        if ext in self._audio_exts:
            return "audio"
        return ""

    def import_media_dialog(self):
        if not self._ensure_edit_mode("导入素材"):
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择素材",
            "",
            "Media Files (*.mp4 *.mov *.webm *.mkv *.avi *.mp3 *.wav *.m4a *.aac *.flac *.ogg)"
        )
        if file_path:
            self.add_media_from_path(file_path)

    def add_media_paths_to_timeline(self, file_paths, start_t=None):
        paths = [path for path in file_paths or [] if path and self._supported_media_path(path)]
        if not paths:
            if hasattr(self, "status_lbl"):
                self.status_lbl.setText("⚠️ 没有可导入的素材。")
            return False

        cursor = max(0.0, float(start_t if start_t is not None else self.current_play_time or 0.0))
        added_video = 0
        added_audio = 0
        for path in paths:
            media_type = self._supported_media_path(path)
            if media_type == "video":
                before = len(self.state.get("video_clips", []) or [])
                if self.add_video_clip_from_path(path, start_t=cursor):
                    clips = self.state.get("video_clips", []) or []
                    new_clip = self.state["video_clips"][self.current_v_idx] if 0 <= self.current_v_idx < len(clips) else None
                    if new_clip:
                        cursor = max(cursor, float(new_clip.get("end", cursor) or cursor))
                    elif len(clips) > before:
                        cursor = max(cursor, float(clips[-1].get("end", cursor) or cursor))
                    added_video += 1
            elif media_type == "audio" and added_audio == 0:
                if self.set_audio_path_from_file(path):
                    added_audio += 1

        if added_video or added_audio:
            if hasattr(self, "status_lbl"):
                parts = []
                if added_video:
                    parts.append(f"{added_video} 段画面已顺序入线")
                if added_audio:
                    parts.append("配音已导入")
                self.status_lbl.setText("；".join(parts) + "。")
            return True
        return False

    def pick_assembly_media_dialog(self):
        if not self._ensure_edit_mode("多素材组接"):
            return
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择要组接的视频素材",
            "",
            "Video Files (*.mp4 *.mov *.webm *.mkv *.avi)"
        )
        if not file_paths:
            return
        self.assembly_media_paths = [
            path for path in file_paths
            if path and os.path.exists(path) and self._supported_media_path(path) == "video"
        ]
        self.refresh_assembly_media_list()
        if hasattr(self, "status_lbl"):
            self.status_lbl.setText(f"已选择 {len(self.assembly_media_paths)} 段素材，点击“一键组接”放入时间线。")

    def clear_assembly_media(self):
        self.assembly_media_paths = []
        self.refresh_assembly_media_list()
        if hasattr(self, "status_lbl"):
            self.status_lbl.setText("组接素材已清空。")

    def refresh_assembly_media_list(self):
        if not hasattr(self, "assembly_list"):
            return
        self.assembly_list.clear()
        for idx, path in enumerate(getattr(self, "assembly_media_paths", []) or []):
            self.assembly_list.addItem(QListWidgetItem(f"{idx + 1}. {os.path.basename(path)}"))
        if not self.assembly_media_paths:
            self.assembly_list.addItem(QListWidgetItem("暂无组接素材，点击“选择素材”"))
        if hasattr(self, "assembly_count_label"):
            self.assembly_count_label.setText(f"{len(self.assembly_media_paths)} 段")

    def assemble_selected_media_to_timeline(self):
        paths = [
            path for path in getattr(self, "assembly_media_paths", []) or []
            if path and os.path.exists(path) and self._supported_media_path(path) == "video"
        ]
        if not paths:
            return QMessageBox.information(self, "没有组接素材", "请先在组接面板里选择几个视频素材。")
        start_t = self.current_play_time if self.edit_mode else None
        if self.assemble_media_paths_to_audio_duration(paths, start_t=start_t):
            if hasattr(self, "status_lbl"):
                self.status_lbl.setText(f"已按音频/工程时长组接 {len(paths)} 段画面素材。")
            self.focus_media_pool()
            return True
        return False

    def _assembly_target_duration(self, paths):
        audio_path = self.state.get("audio_path", "")
        if audio_path and os.path.exists(audio_path):
            audio_dur = float(get_exact_duration(audio_path) or 0.0)
            if audio_dur > 0:
                return audio_dur, "配音"
        content_dur = float(self.state.get("content_duration", 0.0) or 0.0)
        if content_dur > 0:
            return content_dur, "工程"
        state_dur = float(self.state.get("duration", 0.0) or 0.0)
        if state_dur > 1.0:
            return state_dur, "工程"
        return 0.0, "素材"

    def _build_assembly_clip_plan(self, paths, target_duration=0.0):
        valid = []
        for path in paths or []:
            if not path or not os.path.exists(path) or self._supported_media_path(path) != "video":
                continue
            try:
                meta = get_video_import_metadata(path)
                dur = float(meta.get("duration", 0.0) or 0.0)
                duration_info = meta.get("duration_info", {})
            except Exception:
                dur, duration_info = 0.0, {}
            if dur <= 0:
                dur = float(get_video_stream_duration(path) or get_exact_duration(path) or 0.0)
            if dur <= 0:
                dur = 5.0
            valid.append({"path": path, "dur": max(0.05, dur), "duration_info": duration_info})
        if not valid:
            return []

        source_total = sum(item["dur"] for item in valid)
        target_duration = float(target_duration or 0.0)
        if target_duration <= 0:
            target_duration = source_total
        if target_duration <= 0:
            target_duration = len(valid) * 5.0

        timeline_segments = []
        remaining = max(0.05, target_duration)
        for idx, item in enumerate(valid):
            if idx == len(valid) - 1:
                clip_len = remaining
            else:
                weight = item["dur"] / source_total if source_total > 0 else 1.0 / len(valid)
                clip_len = max(0.20, target_duration * weight)
                clip_len = min(clip_len, max(0.20, remaining - 0.20 * (len(valid) - idx - 1)))
                remaining -= clip_len
            source_out = item["dur"]
            timeline_segments.append({
                "path": item["path"],
                "timeline_duration": max(0.05, clip_len),
                "source_duration": item["dur"],
                "source_in": 0.0,
                "source_out": source_out,
                "speed": 1.0,
                "duration_info": item.get("duration_info", {}),
            })
        return timeline_segments

    def assemble_media_paths_to_audio_duration(self, paths, start_t=None):
        if not paths:
            return False
        target_duration, target_label = self._assembly_target_duration(paths)
        plan = self._build_assembly_clip_plan(paths, target_duration)
        if not plan:
            return False

        start = float(start_t if start_t is not None else 0.0)
        cursor = max(0.0, start)
        clips = list(self.state.get("video_clips", []) or [])
        new_clips = []
        for item in plan:
            clip_len = float(item.get("timeline_duration", 0.0) or 0.0)
            if clip_len <= 0:
                continue
            new_clip = {
                "path": self.cloud_import_media_if_needed(item["path"]),
                "start": cursor,
                "end": cursor + clip_len,
                "dur": float(item.get("source_duration", clip_len) or clip_len),
                "scale": 100,
                "volume": 100,
                "duration_probe": item.get("duration_info", {}),
                "source_in": float(item.get("source_in", 0.0) or 0.0),
                "source_out": float(item.get("source_out", item.get("source_duration", clip_len)) or clip_len),
                "speed": float(item.get("speed", 1.0) or 1.0),
                "transition": {"type": "cut", "duration": 0.0},
                "assembly_mode": "audio_matched",
            }
            clips.append(new_clip)
            new_clips.append(new_clip)
            cursor = new_clip["end"]

        if not new_clips:
            return False
        clips.sort(key=lambda c: float(c.get("start", 0.0) or 0.0))
        self.state["video_clips"] = clips
        self.current_v_idx = clips.index(new_clips[0])
        self.current_selected_idx = -1
        self.selected_track = "video"
        self.btn_v.setText("✅ 已组接素材")
        self._queue_preview_proxy_for_clip(new_clips[0], announce=True)
        self._prime_video_preview_source(new_clips[0], announce=True)
        self._recalc_duration()
        self.render_ui_list()
        self.update_timeline_size()
        self.update_floating_subtitle()
        self.refresh_media_pool()
        self.auto_save_cache()
        self.switch_inspector("video")
        self.sync_player_to_time(new_clips[0]["start"])
        self.push_history()
        total = max(0.0, cursor - start)
        if hasattr(self, "status_lbl"):
            self.status_lbl.setText(f"已组接 {len(new_clips)} 段素材，按{target_label}时长分配到 {total:.1f}s。")
        return True

    def dragEnterEvent(self, event):
        if not self.edit_mode:
            event.ignore()
            return
        mime = event.mimeData()
        if mime and any(self._supported_media_path(url.toLocalFile()) for url in mime.urls()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event):
        if not self._ensure_edit_mode("拖放素材"):
            event.ignore()
            return
        accepted = False
        for url in event.mimeData().urls():
            local_path = url.toLocalFile()
            if local_path and self.add_media_from_path(local_path):
                accepted = True
        if accepted:
            event.acceptProposedAction()
        else:
            event.ignore()

    def add_media_from_path(self, file_path):
        media_type = self._supported_media_path(file_path)
        if media_type == "video":
            return self.add_video_clip_from_path(file_path, start_t=self.current_play_time if self.edit_mode else None)
        if media_type == "audio":
            return self.set_audio_path_from_file(file_path)
        if hasattr(self, "status_lbl"):
            self.status_lbl.setText("⚠️ 暂不支持这个素材格式。")
        return False

    def add_media_from_path_at_time(self, file_path, time_sec):
        media_type = self._supported_media_path(file_path)
        drop_time = max(0.0, float(time_sec or 0.0))
        if media_type == "video":
            return self.add_video_clip_from_path(file_path, start_t=drop_time)
        if media_type == "audio":
            if self.set_audio_path_from_file(file_path, record_history=False):
                a_dur = get_exact_duration(self.state.get("audio_path", ""))
                if a_dur > 0:
                    self.state["a_trim"] = [drop_time, drop_time + a_dur]
                    self._recalc_duration()
                    self.update_timeline_size()
                    self.auto_save_cache()
                    self.push_history()
                return True
        if hasattr(self, "status_lbl"):
            self.status_lbl.setText("⚠️ 暂不支持拖入这个素材格式。")
        return False

    def refresh_media_pool(self):
        if not hasattr(self, "media_pool_panel"):
            return
        items = []

        for idx, clip in enumerate(self.state.get("video_clips", []) or []):
            path = clip.get("path", "")
            name = os.path.basename(path) or f"视频 {idx + 1}"
            start = self._format_monitor_time(clip.get("start", 0.0)) if hasattr(self, "_format_monitor_time") else f"{float(clip.get('start', 0.0) or 0.0):.1f}s"
            end = self._format_monitor_time(clip.get("end", 0.0)) if hasattr(self, "_format_monitor_time") else f"{float(clip.get('end', 0.0) or 0.0):.1f}s"
            items.append((f"V{idx + 1}  {name}  {start}-{end}", {"type": "video", "path": path, "index": idx}))
        if self.state.get("audio_path"):
            items.append((f"A1  {os.path.basename(self.state.get('audio_path'))}", {"type": "audio", "path": self.state.get("audio_path"), "index": 0}))
        if self.state.get("music_path"):
            items.append((f"M1  {os.path.basename(self.state.get('music_path'))}", {"type": "music", "path": self.state.get("music_path"), "index": 0}))
        self.media_pool_panel.set_items(items)

    def add_selected_media_pool_item_to_timeline(self):
        if not hasattr(self, "media_pool_panel"):
            return False
        payload = self.media_pool_panel.current_payload()
        if not isinstance(payload, dict) or payload.get("type") == "empty":
            self.import_media_dialog()
            return False
        path = payload.get("path", "")
        media_type = payload.get("type", "")
        if media_type == "video" and path:
            return self.add_video_clip_from_path(path, start_t=self.current_play_time)
        if media_type == "audio" and path:
            return self.set_audio_path_from_file(path)
        if media_type == "music" and path:
            return self.set_music_path_from_file(path)
        return False

    def select_media_pool_payload(self, payload):
        if not isinstance(payload, dict) or payload.get("type") == "empty":
            return
        media_type = payload.get("type")
        idx = int(payload.get("index", 0) or 0)
        if media_type == "video":
            clips = self.state.get("video_clips", []) or []
            if 0 <= idx < len(clips):
                self.current_v_idx = idx
                self.current_selected_idx = -1
                self.selected_track = "video"
                self.switch_inspector("video")
                self.sync_player_to_time(float(clips[idx].get("start", 0.0) or 0.0))
        elif media_type == "audio":
            self.current_selected_idx = -1
            self.selected_track = "audio"
            self.switch_inspector("audio")
        elif media_type == "music":
            self.current_selected_idx = -1
            self.selected_track = "music"
            if hasattr(self, "timeline_widget"):
                self.timeline_widget.sync_from_controller()
            self._update_workspace_status()
