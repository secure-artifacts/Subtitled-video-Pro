import copy

from PyQt6.QtWidgets import QMessageBox

from caption_presets import (
    REFERENCE_NARRATIVE_BLOCK_PRESET,
    REFERENCE_NARRATIVE_CHUNK_MODE,
    built_in_style_presets,
)


class EditTimelineActionsMixin:
    def _find_subtitle_at_time(self, time_sec):
        for i, s in enumerate(self.state.get("subs_data", [])):
            if float(s.get("start", 0.0) or 0.0) < time_sec < float(s.get("end", 0.0) or 0.0):
                return i
        return -1

    def _find_video_at_time(self, time_sec):
        for i, clip in enumerate(self.state.get("video_clips", [])):
            if float(clip.get("start", 0.0) or 0.0) < time_sec < float(clip.get("end", 0.0) or 0.0):
                return i
        return -1

    def split_at_playhead(self):
        if not self._ensure_edit_mode("剪刀切分"):
            return
        t = float(self.current_play_time or 0.0)
        if self.selected_track == "sub":
            idx = self.current_selected_idx if 0 <= self.current_selected_idx < len(self.state.get("subs_data", [])) else self._find_subtitle_at_time(t)
            if idx >= 0 and self._split_subtitle_clip(idx, t):
                return
        if self.selected_track == "video":
            idx = self.current_v_idx if 0 <= self.current_v_idx < len(self.state.get("video_clips", [])) else self._find_video_at_time(t)
            if idx >= 0 and self._split_video_clip(idx, t):
                return
        sub_idx = self._find_subtitle_at_time(t)
        if sub_idx >= 0 and self._split_subtitle_clip(sub_idx, t):
            return
        vid_idx = self._find_video_at_time(t)
        if vid_idx >= 0 and self._split_video_clip(vid_idx, t):
            return
        if hasattr(self, "status_lbl"):
            self.status_lbl.setText("⚠️ 播放头没有落在可切分的片段中间。")

    def _split_subtitle_clip(self, idx, time_sec):
        subs = self.state.get("subs_data", [])
        if not (0 <= idx < len(subs)):
            return False
        clip = subs[idx]
        start = float(clip.get("start", 0.0) or 0.0)
        end = float(clip.get("end", start) or start)
        if not (start + 0.05 < time_sec < end - 0.05):
            return False
        left = copy.deepcopy(clip)
        right = copy.deepcopy(clip)
        left["end"] = time_sec
        right["start"] = time_sec
        left_words, right_words = [], []
        for word in clip.get("words", []):
            w = copy.deepcopy(word)
            ws = float(w.get("start", start) or start)
            we = float(w.get("end", end) or end)
            if we <= time_sec:
                left_words.append(w)
            elif ws >= time_sec:
                right_words.append(w)
            else:
                lw = copy.deepcopy(w); rw = copy.deepcopy(w)
                lw["end"] = time_sec; rw["start"] = time_sec
                left_words.append(lw); right_words.append(rw)
        if left_words and right_words:
            left["words"] = left_words
            right["words"] = right_words
            left["text"] = " ".join(str(w.get("text", "")).strip() for w in left_words).strip()
            right["text"] = " ".join(str(w.get("text", "")).strip() for w in right_words).strip()
        else:
            text = str(clip.get("text", "") or "")
            cut = max(1, min(len(text) - 1, int(len(text) * ((time_sec - start) / max(0.001, end - start)))))
            left["text"] = text[:cut].strip() or text
            right["text"] = text[cut:].strip() or text
            left["words"] = [{"text": left["text"], "start": start, "end": time_sec}]
            right["words"] = [{"text": right["text"], "start": time_sec, "end": end}]
        subs[idx:idx + 1] = [left, right]
        self.state["subs_data"] = sorted(subs, key=lambda x: float(x.get("start", 0.0) or 0.0))
        self.current_selected_idx = self.state["subs_data"].index(right)
        self.selected_track = "sub"
        self.render_ui_list()
        self.switch_inspector("sub")
        self.update_timeline_size()
        self.update_floating_subtitle()
        self.auto_save_cache()
        self.push_history()
        self.status_lbl.setText("✂️ 字幕已在播放头切分。")
        return True

    def _split_video_clip(self, idx, time_sec):
        clips = self.state.get("video_clips", [])
        if not (0 <= idx < len(clips)):
            return False
        clip = clips[idx]
        start = float(clip.get("start", 0.0) or 0.0)
        end = float(clip.get("end", start) or start)
        if not (start + 0.05 < time_sec < end - 0.05):
            return False
        left = copy.deepcopy(clip)
        right = copy.deepcopy(clip)
        left["end"] = time_sec
        right["start"] = time_sec
        source_in = float(clip.get("source_in", 0.0) or 0.0)
        source_out = float(clip.get("source_out", clip.get("dur", end - start)) or clip.get("dur", end - start) or 0.0)
        source_len = max(0.001, source_out - source_in)
        timeline_offset = max(0.0, time_sec - start)
        source_cut = min(source_out, source_in + (timeline_offset % source_len))
        left["source_in"] = source_in
        left["source_out"] = max(source_in, source_cut)
        right["source_in"] = source_cut
        right["source_out"] = source_out
        right.setdefault("transition", {"type": "cut", "duration": 0.0})
        clips[idx:idx + 1] = [left, right]
        self.state["video_clips"] = sorted(clips, key=lambda x: float(x.get("start", 0.0) or 0.0))
        self.current_v_idx = self.state["video_clips"].index(right)
        self.current_selected_idx = -1
        self.selected_track = "video"
        self.switch_inspector("video")
        self._recalc_duration()
        self.auto_save_cache()
        self.push_history()
        self.status_lbl.setText("✂️ 视频片段已在播放头切分。")
        return True

    def apply_simple_transition(self):
        if not self._ensure_edit_mode("添加转场"):
            return
        if self.selected_track == "video" and 0 <= self.current_v_idx < len(self.state.get("video_clips", [])):
            clip = self.state["video_clips"][self.current_v_idx]
            clip["transition"] = {"type": "fade", "duration": 0.35}
            self.timeline_widget.sync_from_controller()
            self.auto_save_cache()
            self.push_history()
            self.status_lbl.setText("✨ 已给当前视频片段标记 0.35s 淡化转场。")
            return
        if self.selected_track == "sub" and 0 <= self.current_selected_idx < len(self.state.get("subs_data", [])):
            clip = self.state["subs_data"][self.current_selected_idx]
            clip.setdefault("style", {}).update({"anim_type": "fade", "pop_speed": 0.28})
            self.sync_inspector_to_clip()
            self.update_floating_subtitle()
            self.timeline_widget.sync_from_controller()
            self.auto_save_cache()
            self.push_history()
            self.status_lbl.setText("✨ 已给当前字幕设置柔和淡入。")
            return
        self.status_lbl.setText("⚠️ 请先选中视频或字幕片段，再添加转场。")

    def apply_reference_two_line_layout(self):
        if not self._ensure_edit_mode("应用参考排版"):
            return
        if not self.state.get("subs_data"):
            return QMessageBox.information(self, "没有字幕", "当前工程还没有字幕片段可以应用排版。")

        if self.current_selected_idx == -1:
            current_clip = self.state["subs_data"][0]
            target_clips = self.state["subs_data"]
        else:
            current_clip = self.state["subs_data"][self.current_selected_idx]
            scope = self.style_scope_combo.currentIndex()
            if scope == 0:
                target_clips = self.state["subs_data"]
            elif scope == 1:
                target_clips = [c for c in self.state["subs_data"] if c.get("track") == current_clip.get("track")]
            else:
                target_clips = [current_clip]

        reference_layout = copy.deepcopy(built_in_style_presets()[REFERENCE_NARRATIVE_BLOCK_PRESET])
        reference_position = reference_layout.pop("__position__", {"pos_x": -23.0, "pos_y": 20.0})
        for clip in target_clips:
            clip["pos_x"] = reference_position["pos_x"]
            clip["pos_y"] = reference_position["pos_y"]
            clip.setdefault("style", {}).update(reference_layout)

        if self.style_scope_combo.currentIndex() == 0:
            self.state["default_pos_x"] = reference_position["pos_x"]
            self.state["default_pos_y"] = reference_position["pos_y"]
            self.default_style.update(reference_layout)

        smart_chunk_mode = REFERENCE_NARRATIVE_CHUNK_MODE
        self.state["chunk_mode"] = smart_chunk_mode
        if hasattr(self, "chunk_mode"):
            self.chunk_mode.blockSignals(True)
            if self.chunk_mode.findText(smart_chunk_mode) < 0:
                self.chunk_mode.addItem(smart_chunk_mode)
            self.chunk_mode.setCurrentText(smart_chunk_mode)
            self.chunk_mode.blockSignals(False)
        if self.current_selected_idx != -1:
            self.sync_inspector_to_clip()
        self._switch_sub_page(2)
        self.update_floating_subtitle()
        self.auto_save_cache()
        self.push_history()
        self.status_lbl.setText("✅ 已应用参考视频四层累积叙事块：14-18词、左下左对齐、小大大小对比。")

    def delete_context_selection(self):
        if not self._ensure_edit_mode("删除片段"):
            return
        if self.selected_track == "design" and self.selected_design_layer_id:
            self.delete_selected_design_layer()
        elif self.selected_track == "music" and self.state.get("music_path"):
            self.remove_music()
        elif self.current_selected_idx != -1:
            self.delete_current_clip()
        elif self.state.get("video_clips"):
            self.remove_last_video_clip()

    def delete_timeline_selection(self, show_message=True):
        if not self._ensure_edit_mode("删除时间线片段"):
            return False
        selected = set(getattr(getattr(self, "timeline_widget", None), "selected_items", set()) or set())
        if not selected:
            before = copy.deepcopy(self._make_history_snapshot())
            self.delete_context_selection()
            return before != self._make_history_snapshot()

        parsed = []
        for key in selected:
            try:
                clip_type, idx_text = str(key).split(":", 1)
                parsed.append((clip_type, int(idx_text)))
            except Exception:
                continue
        if not parsed:
            return False

        changed = False
        video_indices = sorted({idx for clip_type, idx in parsed if clip_type == "video"}, reverse=True)
        sub_indices = sorted({idx for clip_type, idx in parsed if clip_type == "sub"}, reverse=True)
        design_indices = sorted({idx for clip_type, idx in parsed if clip_type == "design"}, reverse=True)

        clips = self.state.get("video_clips", []) or []
        for idx in video_indices:
            if 0 <= idx < len(clips):
                clips.pop(idx)
                changed = True
        if video_indices:
            self.state["video_clips"] = clips
            self.current_v_idx = min(max(0, self.current_v_idx), len(clips) - 1) if clips else -1
            if not clips:
                self.v_wave_pixmap = None
                self.video_thumbs = []
                self.last_video_image = None
                if hasattr(self, "btn_v"):
                    self.btn_v.setText("➕ 导入第一段画面 (MP4)")
                try:
                    self.player.stop()
                    self.player.setSource(QUrl())
                except Exception:
                    pass

        subs = self.state.get("subs_data", []) or []
        for idx in sub_indices:
            if 0 <= idx < len(subs):
                subs.pop(idx)
                changed = True
        if sub_indices:
            self.state["subs_data"] = subs
            self.current_selected_idx = -1

        if any(clip_type == "audio" for clip_type, _ in parsed) and self.state.get("audio_path"):
            self.state["audio_path"] = ""
            self.a_wave_pixmap = None
            try:
                self.audio_player.stop()
                self.audio_player.setSource(QUrl())
            except Exception:
                pass
            if hasattr(self, "btn_a"):
                self.btn_a.setText("🎵 导入独立配音 (可选)")
            changed = True

        if any(clip_type == "music" for clip_type, _ in parsed) and self.state.get("music_path"):
            self.state["music_path"] = ""
            self.state.pop("music_dur", None)
            self.state.pop("music_match_duration", None)
            self.state.pop("music_loop", None)
            try:
                self.music_player.stop()
                self.music_player.setSource(QUrl())
            except Exception:
                pass
            if hasattr(self, "btn_music"):
                self.btn_music.setText("🎼 导入配乐 (可选)")
            changed = True

        if design_indices:
            state = self._current_design_state()
            page = self._design_page(state)
            layers = page.get("layers", []) or []
            delete_ids = {
                layers[idx].get("id", "")
                for idx in design_indices
                if 0 <= idx < len(layers)
            }
            if delete_ids:
                page["layers"] = [layer for layer in layers if layer.get("id", "") not in delete_ids]
                self.selected_design_layer_id = page["layers"][-1].get("id", "") if page["layers"] else ""
                self._commit_design_state(state, sync_controls=True, sync_timeline=False)
                changed = True

        if not changed:
            return False
        if hasattr(self, "timeline_widget"):
            self.timeline_widget.selected_items.clear()
        self._recalc_duration()
        self.render_ui_list()
        self.update_timeline_size()
        self.update_floating_subtitle()
        self.refresh_media_pool()
        self.auto_save_cache()
        self.push_history()
        if hasattr(self, "timeline_widget"):
            self.timeline_widget.sync_from_controller()
        if hasattr(self, "status_lbl"):
            self.status_lbl.setText("🗑️ 已删除选中的时间线片段。")
        if show_message:
            QMessageBox.information(self, "已删除", "选中的时间线片段已删除。")
        return True
