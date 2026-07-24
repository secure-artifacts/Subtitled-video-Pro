import copy
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

import app_config
import media_probe
import media_duration_policy
import media_sync
import playback_events
import playback_clock
import render_range
import render_pipeline_model
import timeline_model
import timeline_interaction
import caption_presets
import subtitle_render_utils
from playback_group import PlayerGroupController, stop_timer
from preview_frame_retry import PreviewFrameRetryPolicy
from preview_overlay_payload import build_preview_overlay_payload
from preview_overlay_sync import build_overlay_sync_script, escape_template_literal, stable_overlay_hash
import preview_overlay_visibility
from preview_seek import slider_value_to_time, time_to_slider_value
from subtitle_activity import active_subtitle_indices, active_subtitle_payload, is_active_subtitle
from font_registry import (
    STATUS_APPROVED,
    STATUS_NONCOMMERCIAL,
    normalize_font_record,
)
from job_control import CooperativeJobControl
from preview_controller import (
    clip_for_time,
    content_duration_for_state,
    playback_duration_for_state,
    video_local_time,
)
from project_io import (
    PROJECT_VERSION,
    ensure_project_schema,
    get_project_folder_paths,
)
from preview_proxy import (
    AUTO_PROXY_PIXEL_COUNT,
    AUTO_PROXY_SIZE_BYTES,
    AUTO_PROXY_DURATION_SECONDS,
    PROXY_STATUS_FAILED,
    PROXY_STATUS_READY,
    build_preview_proxy_command,
    clip_should_auto_proxy,
    prepare_clip_for_preview_proxy,
    preview_proxy_is_ready,
    preview_source_for_clip,
)
from render_timing import active_subtitles_for_frame, build_subtitle_frame_schedule, quantize_sample_time, render_tail_padding_seconds


def _install_pyqt_test_stubs():
    if "PyQt6" in sys.modules:
        return

    pyqt6 = types.ModuleType("PyQt6")
    qtwidgets = types.ModuleType("PyQt6.QtWidgets")
    qtcore = types.ModuleType("PyQt6.QtCore")

    class QWidget:
        def __init__(self, *args, **kwargs):
            pass

        def setLayout(self, *args, **kwargs):
            pass

        def resizeEvent(self, *args, **kwargs):
            pass

    class QVBoxLayout:
        def __init__(self, *args, **kwargs):
            pass

        def setContentsMargins(self, *args, **kwargs):
            pass

        def addWidget(self, *args, **kwargs):
            pass

    class QObject:
        def __init__(self, *args, **kwargs):
            pass

    class Qt:
        class AlignmentFlag:
            AlignCenter = 0

    def pyqtSlot(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

    qtwidgets.QWidget = QWidget
    qtwidgets.QVBoxLayout = QVBoxLayout
    qtcore.Qt = Qt
    qtcore.QObject = QObject
    qtcore.pyqtSlot = pyqtSlot
    sys.modules["PyQt6"] = pyqt6
    sys.modules["PyQt6.QtWidgets"] = qtwidgets
    sys.modules["PyQt6.QtCore"] = qtcore


try:
    from ui_components import (
        align_reference_text_to_timestamps,
        format_subtitle_text_spacing,
        merge_single_word_subtitle_segments,
        normalize_scripture_quote_text,
        normalize_word_timestamps,
        protect_fast_subtitle_pacing,
        rebalance_subtitle_layout,
        render_subtitle_html,
        should_defer_subtitle_break_for_readability,
        tokenize_display_text,
    )
except ModuleNotFoundError as exc:
    if exc.name != "PyQt6":
        raise
    _install_pyqt_test_stubs()
    from ui_components import (
        align_reference_text_to_timestamps,
        format_subtitle_text_spacing,
        merge_single_word_subtitle_segments,
        normalize_scripture_quote_text,
        normalize_word_timestamps,
        protect_fast_subtitle_pacing,
        rebalance_subtitle_layout,
        render_subtitle_html,
        should_defer_subtitle_break_for_readability,
        tokenize_display_text,
    )


class AppConfigTests(unittest.TestCase):
    def test_resolution_to_size_handles_fixed_and_auto_modes(self):
        self.assertEqual(app_config.resolution_to_size("Horizontal 1920x1080"), (1920, 1080))
        self.assertEqual(app_config.resolution_to_size("Square 1080x1080"), (1080, 1080))

        auto_option = app_config.OUTPUT_RESOLUTION_OPTIONS[-1]
        detected = app_config.resolution_to_size(
            auto_option,
            media_path="clip.mp4",
            get_media_size=lambda _path: (1280, 720),
        )
        self.assertEqual(detected, (1280, 720))

        fallback = app_config.resolution_to_size(
            auto_option,
            media_path="clip.mp4",
            get_media_size=lambda _path: (0, 0),
        )
        self.assertEqual(fallback, (1080, 1920))


class ProjectSchemaTests(unittest.TestCase):
    def test_legacy_project_fields_are_backfilled_into_edit_room(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = os.path.join(temp_dir, "Legacy.scomp")
            video_path = os.path.join(temp_dir, "missing-video.mp4")
            audio_path = os.path.join(temp_dir, "missing-audio.mp3")
            source = {
                "project_name": "Legacy",
                "subs_data": [{"text": "hello", "start": 0.0, "end": 1.2}],
                "timeline": [{"path": video_path, "start": 0.0, "end": 1.2}],
                "media_files": {"audio_path": audio_path},
                "duration": 12.5,
            }

            result = ensure_project_schema(source, project_path)
            edit_state = result["room_state"]["edit_room"]

            self.assertEqual(result["project_version"], PROJECT_VERSION)
            self.assertEqual(result["project_path"], project_path)
            self.assertEqual(result["project_dir"], temp_dir)
            self.assertEqual(edit_state["subs_data"], source["subs_data"])
            self.assertEqual(edit_state["video_clips"], source["timeline"])
            self.assertEqual(edit_state["audio_path"], audio_path)
            self.assertEqual(result["subs_data"], source["subs_data"])
            self.assertEqual(result["timeline"], source["timeline"])

    def test_project_folder_scan_excludes_local_asset_and_hidden_dirs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            os.makedirs(os.path.join(temp_dir, "Campaign"))
            os.makedirs(os.path.join(temp_dir, "assets"))
            os.makedirs(os.path.join(temp_dir, ".hidden"))
            os.makedirs(os.path.join(temp_dir, "Campaign", "Nested"))

            self.assertEqual(get_project_folder_paths(temp_dir), ["Campaign"])
            self.assertEqual(
                get_project_folder_paths(temp_dir, recursive=True, max_depth=2),
                [os.path.join("Campaign"), os.path.join("Campaign", "Nested")],
            )


class ChunkModeConfigTests(unittest.TestCase):
    def test_edit_and_batch_expose_smart_interpretation_chunk_mode(self):
        root_dir = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root_dir, "room_edit.py"), "r", encoding="utf-8") as f:
            edit_source = f.read()
        with open(os.path.join(root_dir, "room_batch.py"), "r", encoding="utf-8") as f:
            batch_source = f.read()

        self.assertIn("智能听译 (4-7词，适配双行按词)", edit_source)
        self.assertIn("智能听译 (4-7词，适配双行按词)", batch_source)
        self.assertIn("REFERENCE_NARRATIVE_CHUNK_MODE", edit_source)
        self.assertIn("REFERENCE_NARRATIVE_CHUNK_MODE", batch_source)
        self.assertIn("is_reference_narrative_chunk_mode", edit_source)
        self.assertIn("tiktok_smart", batch_source)


    def test_reference_narrative_block_preset_is_available(self):
        presets = caption_presets.built_in_style_presets()
        style = presets[caption_presets.REFERENCE_NARRATIVE_BLOCK_PRESET]

        self.assertTrue(caption_presets.is_reference_narrative_chunk_mode(caption_presets.REFERENCE_NARRATIVE_CHUNK_MODE))
        self.assertEqual(style["layout_mode"], "narrative_block")
        self.assertEqual(style["caption_build_mode"], "cumulative_block")
        self.assertEqual(style["caption_block_min_words"], 14)
        self.assertEqual(style["caption_block_max_words"], 18)

    def test_fixed_chunk_modes_accept_word_and_character_aliases(self):
        cases = [
            ("\u53cc\u8bcd", 2),
            ("\u53cc\u5b57", 2),
            ("2\u8bcd", 2),
            ("2\u5b57", 2),
            ("\u4e8c\u8bcd", 2),
            ("\u4e8c\u5b57", 2),
            ("\u4e09\u5b57", 3),
            ("3\u5b57", 3),
            ("\u56db\u5b57", 4),
            ("4\u5b57", 4),
        ]

        for mode, expected in cases:
            with self.subTest(mode=mode):
                self.assertEqual(caption_presets.fixed_word_count_for_chunk_mode(mode), expected)

        self.assertTrue(caption_presets.is_exact_single_word_chunk_mode("1\u5b57"))
        self.assertEqual(caption_presets.pacing_merge_word_limit_for_chunk_mode("2\u5b57"), 0)
        self.assertEqual(caption_presets.pacing_merge_word_limit_for_chunk_mode("\u667a\u80fd\u91cd\u70b9\u77ed\u53e5 (3-4\u8bcd\u4e3a\u4e3b)"), 4)
        self.assertEqual(caption_presets.pacing_merge_word_limit_for_chunk_mode("\u667a\u80fd\u542c\u8bd1 (4-7\u8bcd)"), 7)

    def test_edit_timeline_controller_interfaces_are_present(self):
        root_dir = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root_dir, "room_edit.py"), "r", encoding="utf-8") as f:
            edit_source = f.read()
        with open(os.path.join(root_dir, "timeline_engine.py"), "r", encoding="utf-8") as f:
            timeline_source = f.read()

        self.assertIn("def delete_timeline_selection", edit_source)
        self.assertIn("def add_media_from_path_at_time", edit_source)
        self.assertIn("def _make_history_snapshot", edit_source)
        self.assertIn("def refresh_media_pool", edit_source)
        self.assertIn("delete_timeline_selection", timeline_source)
        self.assertIn("add_media_from_path_at_time", timeline_source)


class FontRegistryTests(unittest.TestCase):
    def test_legacy_approved_fonts_are_downgraded_to_noncommercial(self):
        record = normalize_font_record(
            "Custom Sans",
            {"status": STATUS_APPROVED, "notes": "Old approval"},
        )

        self.assertEqual(record["status"], STATUS_NONCOMMERCIAL)
        self.assertEqual(record["commercial_use"], "personal_only_registered")
        self.assertIn("Legacy approved entries", record["notes"])
        self.assertEqual(record["category"], "restricted_noncommercial")


class RenderTimingTests(unittest.TestCase):
    def test_tail_padding_env_value_is_clamped(self):
        with mock.patch.dict(os.environ, {"SUBTITLE_RENDER_TAIL_PAD": "99"}, clear=False):
            self.assertEqual(render_tail_padding_seconds(), 5.0)
        with mock.patch.dict(os.environ, {"SUBTITLE_RENDER_TAIL_PAD": "-2"}, clear=False):
            self.assertEqual(render_tail_padding_seconds(), 0.0)

    def test_frame_schedule_respects_extra_times_and_total_duration(self):
        schedule = build_subtitle_frame_schedule([], 1.0, extra_times=[0.25, 0.75])

        self.assertEqual(schedule, [(0.0, 0.25), (0.25, 0.5), (0.75, 0.25)])

    def test_continuous_subtitle_motion_adds_intermediate_samples(self):
        with mock.patch.dict(os.environ, {"SUBTITLE_CONTINUOUS_FPS": "10"}, clear=False):
            schedule = build_subtitle_frame_schedule(
                [
                    {
                        "start": 0.0,
                        "end": 0.5,
                        "text": "hello",
                        "style": {"anim_type": "roll_up"},
                    }
                ],
                0.5,
            )

        self.assertGreater(len(schedule), 2)
        self.assertEqual(schedule[0][0], 0.0)
        self.assertAlmostEqual(sum(duration for _, duration in schedule), 0.5, places=3)

    def test_subtitle_start_sampling_does_not_round_before_start(self):
        sub = {"start": 1.2344, "end": 2.0, "text": "visible"}

        self.assertEqual(quantize_sample_time(sub["start"]), 1.235)
        active = active_subtitles_for_frame([sub], 1.235, 0.4)
        self.assertEqual(len(active), 1)
        self.assertIs(active[0][0], sub)
        self.assertEqual(active[0][1], 1.235)
        self.assertEqual(active_subtitles_for_frame([sub], 1.234, 0.4), [])

    def test_short_subtitle_inside_frame_is_sampled(self):
        sub = {"start": 0.010, "end": 0.018, "text": "God"}

        active = active_subtitles_for_frame([sub], 0.0, 1 / 30)

        self.assertEqual(len(active), 1)
        self.assertIs(active[0][0], sub)
        self.assertAlmostEqual(active[0][1], 0.010, places=3)


class RenderRangeTests(unittest.TestCase):
    def test_disabled_range_uses_full_duration(self):
        normalized = render_range.normalize_render_range({"render_range": {"enabled": False, "start": 2.0, "end": 4.0}}, 9.0)

        self.assertFalse(normalized["enabled"])
        self.assertEqual(normalized["start"], 0.0)
        self.assertEqual(normalized["end"], 9.0)
        self.assertEqual(normalized["duration"], 9.0)

    def test_enabled_range_clamps_to_total_duration(self):
        state = {}
        normalized = render_range.set_render_range(state, enabled=True, start=3.0, end=20.0, total_duration=10.0)

        self.assertTrue(normalized["enabled"])
        self.assertEqual(normalized["start"], 3.0)
        self.assertEqual(normalized["end"], 10.0)
        self.assertEqual(normalized["duration"], 7.0)
        self.assertEqual(state["render_range"], {"enabled": True, "start": 3.0, "end": 10.0})


class RenderCanvasLayerTests(unittest.TestCase):
    def test_canvas_layer_rect_covers_canvas_by_default(self):
        rect = render_pipeline_model.canvas_layer_rect(1080, 1920, 3840, 2160, scale=1.0)

        self.assertEqual((rect.width, rect.height), (3413, 1920))
        self.assertEqual(rect.x, -1166)
        self.assertEqual(rect.y, 0)

    def test_canvas_layer_rect_can_preserve_original_size(self):
        rect = render_pipeline_model.canvas_layer_rect(1080, 1920, 3840, 2160, scale=1.0, fit="original")

        self.assertEqual((rect.width, rect.height), (3840, 2160))
        self.assertEqual(rect.x, -1380)
        self.assertEqual(rect.y, -120)

    def test_canvas_layer_rect_applies_percent_position(self):
        rect = render_pipeline_model.canvas_layer_rect(1080, 1920, 1080, 1920, scale=1.0, pos_x=10, pos_y=-5)

        self.assertEqual((rect.width, rect.height), (1080, 1920))
        self.assertEqual(rect.x, 108)
        self.assertEqual(rect.y, -96)

    def test_ffmpeg_canvas_layer_helpers_are_stable(self):
        self.assertEqual(
            render_pipeline_model.ffmpeg_canvas_source(1080, 1920, 2.5),
            "color=c=black:s=1080x1920:d=2.500,format=rgba[canvas]",
        )
        self.assertEqual(render_pipeline_model.ffmpeg_layer_scale_filter(1.25), "scale=iw*1.250000:ih*1.250000:flags=lanczos")
        self.assertEqual(
            render_pipeline_model.ffmpeg_layer_scale_filter(1.0, 1080, 1920),
            "scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,setsar=1",
        )
        self.assertEqual(
            render_pipeline_model.ffmpeg_layer_overlay_xy(10, -5),
            ("(W-w)/2+(W*10.000/100)", "(H-h)/2+(H*-5.000/100)"),
        )

    def test_ffconcat_entries_escape_windows_and_quote_paths(self):
        entry = render_pipeline_model.ffconcat_file_entry(r"C:\media\Bob's clip.mp4", 1.25)
        inout = render_pipeline_model.ffconcat_inout_entry(r"C:\media\Bob's clip.mp4", 0.5, 2.75)

        self.assertIn("file 'C:/media/Bob'\\''s clip.mp4'", entry)
        self.assertIn("duration 1.250", entry)
        self.assertIn("inpoint 0.500", inout)
        self.assertIn("outpoint 2.750", inout)


class PreviewProxyTests(unittest.TestCase):
    def test_existing_proxy_is_reused_for_matching_source_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "camera clip.mov")
            with open(source, "wb") as f:
                f.write(b"source-video")

            clip = {"path": source}
            proxy_path, _, needs_generation = prepare_clip_for_preview_proxy(clip)
            self.assertTrue(needs_generation)
            os.makedirs(os.path.dirname(proxy_path), exist_ok=True)
            with open(proxy_path, "wb") as f:
                f.write(b"x" * 2048)
            self.addCleanup(lambda path=proxy_path: os.path.exists(path) and os.remove(path))

            proxy_path, fingerprint, needs_generation = prepare_clip_for_preview_proxy(clip)

            self.assertFalse(needs_generation)
            self.assertEqual(clip["preview_proxy_status"], PROXY_STATUS_READY)
            self.assertTrue(preview_proxy_is_ready(clip))
            self.assertEqual(preview_source_for_clip(clip), proxy_path)
            self.assertEqual(clip["preview_proxy_fingerprint"], fingerprint)

    def test_stale_proxy_requires_regeneration_after_source_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "camera.mov")
            with open(source, "wb") as f:
                f.write(b"old")

            clip = {"path": source}
            _, _, needs_generation = prepare_clip_for_preview_proxy(clip)
            self.assertTrue(needs_generation)

            stale_fingerprint = dict(clip["preview_proxy_fingerprint"])
            with open(source, "ab") as f:
                f.write(b"new")

            _, fresh_fingerprint, needs_generation = prepare_clip_for_preview_proxy(clip)

            self.assertTrue(needs_generation)
            self.assertNotEqual(stale_fingerprint, fresh_fingerprint)
            self.assertEqual(preview_source_for_clip(clip), source)

    def test_failed_proxy_generation_is_not_retried_until_source_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "camera.mov")
            with open(source, "wb") as f:
                f.write(b"source-video")

            clip = {"path": source}
            proxy_path, fingerprint, needs_generation = prepare_clip_for_preview_proxy(clip)
            self.assertTrue(needs_generation)

            clip["preview_proxy_status"] = PROXY_STATUS_FAILED
            clip["preview_proxy_error"] = "ffmpeg failed"
            retry_proxy_path, retry_fingerprint, needs_generation = prepare_clip_for_preview_proxy(clip)

            self.assertEqual(retry_proxy_path, proxy_path)
            self.assertEqual(retry_fingerprint, fingerprint)
            self.assertFalse(needs_generation)
            self.assertEqual(clip["preview_proxy_status"], PROXY_STATUS_FAILED)
            self.assertEqual(preview_source_for_clip(clip), source)

    def test_proxy_ffmpeg_command_targets_lightweight_preview_format(self):
        cmd = build_preview_proxy_command("ffmpeg", "input.mov", "proxy.mp4")
        joined = " ".join(cmd)

        self.assertIn("scale=-2:540", joined)
        self.assertIn("fps=24", joined)
        self.assertIn("libx264", cmd)
        self.assertIn("veryfast", cmd)
        self.assertIn("-threads", cmd)
        self.assertIn("1", cmd)
        self.assertNotIn("-shortest", cmd)
        self.assertEqual(cmd[-1], "proxy.mp4")

        low_cmd = build_preview_proxy_command("ffmpeg", "input.mov", "proxy.mp4", proxy_height=360, proxy_fps=18, proxy_crf=30)
        low_joined = " ".join(low_cmd)
        self.assertIn("scale=-2:360", low_joined)
        self.assertIn("fps=18", low_joined)
        self.assertIn("30", low_cmd)

    def test_smart_proxy_only_targets_large_or_long_clips(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "camera.mov")
            with open(source, "wb") as f:
                f.write(b"x" * 1024)

            self.assertFalse(clip_should_auto_proxy({"path": source, "dur": AUTO_PROXY_DURATION_SECONDS - 1}))
            self.assertTrue(clip_should_auto_proxy({"path": source, "dur": AUTO_PROXY_DURATION_SECONDS + 1}))
            self.assertFalse(clip_should_auto_proxy({"path": source, "dur": 17.0, "width": 1920, "height": 1080}))
            self.assertTrue(clip_should_auto_proxy({"path": source, "dur": 17.0, "width": 3840, "height": 2160}))

        with mock.patch("preview_proxy.os.path.getsize", return_value=AUTO_PROXY_SIZE_BYTES + 1):
            self.assertTrue(clip_should_auto_proxy({"path": "large.mov", "dur": 1.0}))
        self.assertEqual(AUTO_PROXY_PIXEL_COUNT, 1920 * 1080)


class PreviewControllerTests(unittest.TestCase):
    def test_playback_duration_prefers_timeline_content_over_stale_state_duration(self):
        state = {
            "duration": 99.0,
            "video_clips": [{"start": 0.0, "end": 4.0, "dur": 4.0}],
            "subs_data": [{"start": 0.0, "end": 3.0, "text": "hello"}],
        }

        self.assertEqual(content_duration_for_state(state), 4.0)
        self.assertEqual(playback_duration_for_state(state), 4.0)

    def test_clip_lookup_and_looped_local_time_are_stable(self):
        clip = {"start": 0.0, "end": 4.0, "dur": 4.0, "source_in": 0.0, "source_out": 2.0}
        clips = [clip]

        self.assertEqual(clip_for_time(clips, 99.0), (0, clip))
        self.assertAlmostEqual(video_local_time(clip, 2.5), 0.5)

    def test_video_local_time_respects_clip_speed(self):
        clip = {"start": 0.0, "end": 2.0, "dur": 4.0, "source_in": 0.0, "source_out": 4.0, "speed": 2.0}

        self.assertAlmostEqual(video_local_time(clip, 1.25), 2.5)


class TimelineModelTests(unittest.TestCase):
    def test_append_video_clip_uses_timeline_end_and_sorts(self):
        state = {
            "video_clips": [
                {"path": "late.mp4", "start": 4.0, "end": 5.0},
                {"path": "early.mp4", "start": 0.0, "end": 1.0},
            ]
        }

        clip, idx = timeline_model.append_video_clip_to_state(state, "new.mp4", 2.0)

        self.assertEqual(clip["start"], 5.0)
        self.assertEqual(clip["end"], 7.0)
        self.assertEqual(idx, 2)
        self.assertEqual([item["path"] for item in state["video_clips"]], ["early.mp4", "late.mp4", "new.mp4"])

    def test_split_video_clip_maps_looped_source_time(self):
        clips = [{"path": "loop.mp4", "start": 0.0, "end": 5.0, "dur": 2.0, "source_in": 0.0, "source_out": 2.0}]

        result = timeline_model.split_video_clip_at(clips, 0, 3.0)

        self.assertIsNotNone(result)
        new_clips, left, right = result
        self.assertEqual(len(new_clips), 2)
        self.assertEqual(left["end"], 3.0)
        self.assertEqual(right["start"], 3.0)
        self.assertAlmostEqual(left["source_out"], 1.0)
        self.assertAlmostEqual(right["source_in"], 1.0)
        self.assertAlmostEqual(right["source_out"], 2.0)

    def test_split_video_clip_maps_speed_adjusted_source_time(self):
        clips = [{"path": "fast.mp4", "start": 0.0, "end": 2.0, "dur": 4.0, "source_in": 0.0, "source_out": 4.0, "speed": 2.0}]

        result = timeline_model.split_video_clip_at(clips, 0, 1.0)

        self.assertIsNotNone(result)
        _, left, right = result
        self.assertAlmostEqual(left["source_out"], 2.0)
        self.assertAlmostEqual(right["source_in"], 2.0)

    def test_set_video_speed_adjusts_timeline_duration(self):
        state = {"video_clips": [{"path": "clip.mp4", "start": 3.0, "end": 7.0, "dur": 4.0, "source_in": 0.0, "source_out": 4.0, "speed": 1.0}]}

        changed, idx = timeline_model.set_video_clip_speed_in_state(state, 0, 2.0)

        self.assertTrue(changed)
        self.assertEqual(idx, 0)
        self.assertEqual(state["video_clips"][0]["speed"], 2.0)
        self.assertAlmostEqual(state["video_clips"][0]["end"], 5.0)

    def test_fit_video_clip_to_duration_sets_speed_from_source_coverage(self):
        state = {"video_clips": [{"path": "clip.mp4", "start": 1.0, "end": 13.0, "dur": 12.0, "source_in": 0.0, "source_out": 12.0, "speed": 1.0}]}

        changed, idx, speed, clamped = timeline_model.fit_video_clip_to_duration_in_state(state, 0, 4.0)

        self.assertTrue(changed)
        self.assertEqual(idx, 0)
        self.assertFalse(clamped)
        self.assertAlmostEqual(speed, 3.0)
        self.assertAlmostEqual(state["video_clips"][0]["speed"], 3.0)
        self.assertAlmostEqual(state["video_clips"][0]["end"], 5.0)

    def test_fit_video_clip_to_duration_reports_speed_clamp(self):
        state = {"video_clips": [{"path": "clip.mp4", "start": 0.0, "end": 100.0, "dur": 100.0, "source_in": 0.0, "source_out": 100.0, "speed": 1.0}]}

        changed, idx, speed, clamped = timeline_model.fit_video_clip_to_duration_in_state(state, 0, 1.0)

        self.assertTrue(changed)
        self.assertEqual(idx, 0)
        self.assertTrue(clamped)
        self.assertEqual(speed, timeline_model.MAX_CLIP_SPEED)
        self.assertAlmostEqual(state["video_clips"][0]["end"], 12.5)

    def test_update_video_timing_keeps_selected_clip_after_sort(self):
        state = {
            "video_clips": [
                {"path": "a.mp4", "start": 0.0, "end": 1.0},
                {"path": "b.mp4", "start": 2.0, "end": 3.0},
            ]
        }

        changed, idx = timeline_model.update_video_clip_timing_in_state(state, 0, 4.0, 5.0)

        self.assertTrue(changed)
        self.assertEqual(idx, 1)
        self.assertEqual([item["path"] for item in state["video_clips"]], ["b.mp4", "a.mp4"])

    def test_render_duration_uses_trimmed_audio_and_tail_padding(self):
        state = {
            "duration": 99.0,
            "audio_path": "voice.wav",
            "a_trim": [2.0, 6.0],
            "subs_data": [{"start": 0.0, "end": 3.0}],
        }

        render_duration, content_duration = timeline_model.render_duration_for_state(
            state,
            exact_duration=lambda _path: 20.0,
            tail_padding=0.5,
        )

        self.assertEqual(content_duration, 4.0)
        self.assertEqual(render_duration, 4.5)


class TimelineInteractionTests(unittest.TestCase):
    def test_snap_time_uses_configurable_step(self):
        self.assertAlmostEqual(timeline_interaction.snap_time(1.027, enabled=True, step=0.05), 1.05)
        self.assertAlmostEqual(timeline_interaction.snap_time(1.027, enabled=False, step=0.05), 1.027)

    def test_snap_time_with_points_prefers_nearby_edges_then_grid(self):
        self.assertAlmostEqual(
            timeline_interaction.snap_time_with_points(
                2.03,
                enabled=True,
                step=0.05,
                points=[1.0, 2.0, 3.0],
                max_point_distance=0.04,
            ),
            2.0,
        )
        self.assertAlmostEqual(
            timeline_interaction.snap_time_with_points(
                2.08,
                enabled=True,
                step=0.05,
                points=[1.0, 2.0, 3.0],
                max_point_distance=0.04,
            ),
            2.1,
        )

    def test_clamp_timing_preserves_minimum_duration(self):
        self.assertEqual(timeline_interaction.clamp_timing(-2.0, -1.0, min_duration=0.2), (0.0, 0.2))
        self.assertEqual(timeline_interaction.clamp_timing(3.0, 3.0, min_duration=0.2), (3.0, 3.2))

    def test_selection_helpers_toggle_and_replace(self):
        selection = timeline_interaction.update_selection(set(), "video", 1, additive=False)
        self.assertEqual(selection, {"video:1"})
        selection = timeline_interaction.update_selection(selection, "sub", 2, additive=True)
        self.assertEqual(selection, {"video:1", "sub:2"})
        selection = timeline_interaction.update_selection(selection, "video", 1, additive=True)
        self.assertEqual(selection, {"sub:2"})
        self.assertEqual(timeline_interaction.parse_item_key("sub:2"), ("sub", 2))

    def test_shift_timing_clamps_against_zero(self):
        self.assertEqual(timeline_interaction.shift_timing(1.0, 3.0, 2.0), (3.0, 5.0))
        self.assertEqual(timeline_interaction.shift_timing(1.0, 3.0, -5.0), (0.0, 2.0))

    def test_format_timing_label_includes_track_and_duration(self):
        label = timeline_interaction.format_timing_label(62.0, 65.5, "V1")

        self.assertIn("V1", label)
        self.assertIn("01:02.0", label)
        self.assertIn("03.5s", label)


class PlaybackClockTests(unittest.TestCase):
    def test_start_resets_when_already_at_end(self):
        now = [10.0]
        clock = playback_clock.PlaybackClock(now_func=lambda: now[0])

        self.assertEqual(clock.start(4.99, 5.0), 0.0)
        self.assertEqual(clock.current_time, 0.0)

    def test_tick_advances_from_anchor_and_loops_at_end(self):
        now = [0.0]
        clock = playback_clock.PlaybackClock(now_func=lambda: now[0])
        clock.start(1.0, 5.0)
        now[0] = 2.25

        state, current_time = clock.tick(5.0, loop_enabled=False)

        self.assertEqual(state, "playing")
        self.assertAlmostEqual(current_time, 3.25)

        now[0] = 4.1
        state, current_time = clock.tick(5.0, loop_enabled=True)

        self.assertEqual(state, "loop")
        self.assertEqual(current_time, 0.0)
        self.assertEqual(clock.current_time, 0.0)

    def test_seek_relative_is_clamped_to_duration(self):
        self.assertEqual(playback_clock.seek_relative_time(0.2, -1.0, 5.0), 0.0)
        self.assertEqual(playback_clock.seek_relative_time(4.8, 1.0, 5.0), 5.0)


class MediaSyncTests(unittest.TestCase):
    def test_sync_decision_seeks_on_force_source_change_or_large_drift(self):
        stable = media_sync.sync_decision("a.mp4", "a.mp4", 1.0, 1.1, drift_limit=0.3)
        self.assertFalse(stable["source_changed"])
        self.assertFalse(stable["seek"])

        changed = media_sync.sync_decision("a.mp4", "b.mp4", 1.0, 1.1, drift_limit=0.3)
        self.assertTrue(changed["source_changed"])
        self.assertTrue(changed["seek"])

        forced = media_sync.sync_decision("a.mp4", "a.mp4", 1.0, 1.1, drift_limit=0.3, force_seek=True)
        self.assertTrue(forced["seek"])

        drifted = media_sync.sync_decision("a.mp4", "a.mp4", 1.0, 1.5, drift_limit=0.3)
        self.assertTrue(drifted["seek"])

    def test_music_local_time_loops_or_clamps(self):
        self.assertEqual(media_sync.music_local_time(12.5, 5.0, loop_enabled=True), 2.5)
        self.assertEqual(media_sync.music_local_time(12.5, 5.0, loop_enabled=False), 5.0)
        self.assertEqual(media_sync.music_local_time(12.5, 0.0, loop_enabled=True), 12.5)

    def test_video_drift_limit_is_looser_while_playing(self):
        self.assertGreater(media_sync.video_drift_limit(True), media_sync.video_drift_limit(False))


class PlaybackEventsTests(unittest.TestCase):
    def test_video_end_resyncs_mid_timeline_and_restarts_near_end(self):
        self.assertEqual(
            playback_events.video_end_action(1.0, 5.0, loop_enabled=True),
            playback_events.ACTION_RESYNC_VIDEO,
        )
        self.assertEqual(
            playback_events.video_end_action(4.95, 5.0, loop_enabled=True),
            playback_events.ACTION_RESTART_LOOP,
        )
        self.assertEqual(
            playback_events.video_end_action(4.95, 5.0, loop_enabled=False),
            playback_events.ACTION_STOP_AT_END,
        )

    def test_audio_end_pauses_mid_timeline_then_obeys_preview_loop_at_end(self):
        self.assertEqual(
            playback_events.audio_end_action(1.0, 5.0, loop_enabled=True),
            playback_events.ACTION_PAUSE_AUDIO,
        )
        self.assertEqual(
            playback_events.audio_end_action(4.95, 5.0, loop_enabled=True),
            playback_events.ACTION_RESTART_LOOP,
        )
        self.assertEqual(
            playback_events.audio_end_action(4.95, 5.0, loop_enabled=False),
            playback_events.ACTION_STOP_AT_END,
        )

    def test_music_end_resyncs_when_music_looping_and_stops_with_preview_loop_off(self):
        self.assertEqual(
            playback_events.music_end_action(1.0, 5.0, preview_loop_enabled=True, music_loop_enabled=True),
            playback_events.ACTION_RESYNC_MUSIC,
        )
        self.assertEqual(
            playback_events.music_end_action(1.0, 5.0, preview_loop_enabled=True, music_loop_enabled=False),
            playback_events.ACTION_PAUSE_MUSIC,
        )
        self.assertEqual(
            playback_events.music_end_action(4.95, 5.0, preview_loop_enabled=False, music_loop_enabled=True),
            playback_events.ACTION_STOP_AT_END,
        )


class PreviewFrameRetryTests(unittest.TestCase):
    def test_retry_is_cooperative_and_limited(self):
        policy = PreviewFrameRetryPolicy(max_retries=2, retry_delay_ms=50)

        self.assertFalse(policy.request_retry(has_video_clips=False, has_frame=False))
        self.assertTrue(policy.request_retry(has_video_clips=True, has_frame=False))
        self.assertTrue(policy.pending)
        self.assertEqual(policy.count, 1)
        self.assertFalse(policy.request_retry(has_video_clips=True, has_frame=False))

        policy.mark_retry_window_elapsed()
        self.assertTrue(policy.request_retry(has_video_clips=True, has_frame=False))
        self.assertEqual(policy.count, 2)

        policy.mark_retry_window_elapsed()
        self.assertFalse(policy.request_retry(has_video_clips=True, has_frame=False))

    def test_frame_ready_and_source_change_reset_retry_state(self):
        policy = PreviewFrameRetryPolicy(max_retries=2)
        self.assertTrue(policy.request_retry(has_video_clips=True, has_frame=False))

        policy.mark_frame_ready()

        self.assertFalse(policy.pending)
        self.assertEqual(policy.count, 0)
        self.assertTrue(policy.request_retry(has_video_clips=True, has_frame=False))

        policy.reset_for_source_change()

        self.assertFalse(policy.pending)
        self.assertEqual(policy.count, 0)


class PlaybackGroupTests(unittest.TestCase):
    class FakePlayer:
        def __init__(self):
            self.calls = []

        def play(self):
            self.calls.append("play")

        def pause(self):
            self.calls.append("pause")

        def setPosition(self, value):
            self.calls.append(("setPosition", value))

        def stop(self):
            self.calls.append("stop")

    def test_play_and_pause_only_touch_enabled_optional_tracks(self):
        video = self.FakePlayer()
        audio = self.FakePlayer()
        music = self.FakePlayer()
        group = PlayerGroupController(video, audio, music)

        group.play(has_audio=True, has_music=False)
        group.pause(has_audio=False, has_music=True)

        self.assertEqual(video.calls, ["play", "pause"])
        self.assertEqual(audio.calls, ["play"])
        self.assertEqual(music.calls, ["pause"])

    def test_audio_position_and_timer_stop_are_duck_typed(self):
        audio = self.FakePlayer()
        timer = self.FakePlayer()
        group = PlayerGroupController(audio_player=audio)

        self.assertTrue(group.set_audio_position(1200))
        self.assertTrue(stop_timer(timer))
        self.assertFalse(stop_timer(None))
        self.assertEqual(audio.calls, [("setPosition", 1200)])
        self.assertEqual(timer.calls, ["stop"])


class PreviewSeekTests(unittest.TestCase):
    def test_slider_value_to_time_clamps_edges(self):
        self.assertEqual(slider_value_to_time(5000, 10.0, 10000), 5.0)
        self.assertEqual(slider_value_to_time(-10, 10.0, 10000), 0.0)
        self.assertEqual(slider_value_to_time(12000, 10.0, 10000), 10.0)
        self.assertEqual(slider_value_to_time(5000, 0.0, 10000), 0.0)

    def test_time_to_slider_value_clamps_edges(self):
        self.assertEqual(time_to_slider_value(5.0, 10.0, 10000), 5000)
        self.assertEqual(time_to_slider_value(-2.0, 10.0, 10000), 0)
        self.assertEqual(time_to_slider_value(15.0, 10.0, 10000), 10000)
        self.assertEqual(time_to_slider_value(5.0, 0.0, 10000), 0)


class SubtitleActivityTests(unittest.TestCase):
    def test_active_subtitle_payload_marks_new_and_selected_items(self):
        subtitles = [
            {"start": 0.0, "end": 1.0, "text": "old", "track": 1, "style": {"box_width": 120}},
            {"start": 0.5, "end": 2.0, "text": "selected", "pos_x": 3.0, "pos_y": 4.0},
            {"start": 3.0, "end": 4.0, "text": "later"},
        ]

        payload = active_subtitle_payload(
            subtitles,
            0.75,
            1080,
            selected_idx=1,
            active_cache={0},
            render_html=lambda sub, time_sec, width: f"{sub['text']}@{time_sec:.2f}/{width}",
        )

        self.assertEqual([item["idx"] for item in payload], [0, 1])
        self.assertFalse(payload[0]["isNew"])
        self.assertTrue(payload[1]["isNew"])
        self.assertTrue(payload[1]["isSelected"])
        self.assertEqual(payload[0]["box_width"], 120)
        self.assertEqual(payload[1]["pos_x"], 3.0)
        self.assertEqual(active_subtitle_indices(payload), {0, 1})

    def test_active_subtitle_payload_passes_canvas_height_when_available(self):
        payload = active_subtitle_payload(
            [{"start": 0.0, "end": 1.0, "text": "fit"}],
            0.5,
            1080,
            render_html=lambda sub, time_sec, width, height: f"{sub['text']}:{width}x{height}",
            project_height=1920,
        )

        self.assertEqual(payload[0]["htmlText"], "fit:1080x1920")

    def test_active_subtitle_range_is_inclusive_and_tolerant(self):
        subtitle = {"start": "1.0", "end": "2.0"}

        self.assertTrue(is_active_subtitle(subtitle, 1.0))
        self.assertTrue(is_active_subtitle(subtitle, 2.0))
        self.assertFalse(is_active_subtitle(subtitle, 2.01))
        self.assertFalse(is_active_subtitle({"start": "bad", "end": "bad"}, 1.0))


class SubtitleRenderUtilsTests(unittest.TestCase):
    def test_html_text_attr_and_multiline_escape_correctly(self):
        self.assertEqual(subtitle_render_utils.html_text("<b>Tom & Jerry</b>"), "&lt;b&gt;Tom &amp; Jerry&lt;/b&gt;")
        self.assertEqual(subtitle_render_utils.html_attr('"quoted" & <tag>'), "&quot;quoted&quot; &amp; &lt;tag&gt;")
        self.assertEqual(subtitle_render_utils.html_multiline_text("a\n<b>"), "a<br>&lt;b&gt;")

    def test_css_font_stack_escapes_primary_and_deduplicates_fallback(self):
        stack = subtitle_render_utils.css_font_stack(r"Noto Sans SC\Bob's")

        self.assertIn(r"'Noto Sans SC\\Bob\'s'", stack)
        self.assertIn("'TikTok Sans'", stack)
        self.assertTrue(stack.endswith("sans-serif"))
        self.assertEqual(subtitle_render_utils.css_font_stack("Arial").count("'Arial'"), 1)

    def test_canva_fit_background_renders_line_chips(self):
        html = render_subtitle_html(
            {
                "start": 0.0,
                "end": 2.0,
                "text": "hello world",
                "words": [
                    {"text": "hello", "start": 0.0, "end": 1.0},
                    {"text": "\nworld", "start": 1.0, "end": 2.0},
                ],
                "style": {
                    "bg_mode": "canva_fit",
                    "bg_color": "#101010",
                    "bg_alpha": 72,
                    "bg_radius": 18,
                    "bg_pad_left": 22,
                    "bg_pad_right": 18,
                    "bg_pad_top": 7,
                    "bg_pad_bottom": 9,
                    "box_width": 58,
                    "box_layout": "auto",
                    "hl_style": "canva_frame",
                    "size": 90,
                },
            },
            0.5,
            1080,
        )

        self.assertIn("canva-fit-bg", html)
        self.assertIn("box-decoration-break: clone", html)
        self.assertIn("display: inline", html)
        self.assertIn("white-space: normal", html)
        self.assertGreaterEqual(html.count("display:block"), 2)
        self.assertIn("padding: 0.6481vw 1.6667vw 0.8333vw 2.0370vw", html)
        self.assertIn("box-shadow: 0 0 0", html)
        self.assertIn("rgba(255, 0, 80", html)

    def test_canva_fit_keeps_preset_highlight_color(self):
        html = render_subtitle_html(
            {
                "start": 0.0,
                "end": 2.0,
                "text": "hello world",
                "words": [
                    {"text": "hello", "start": 0.0, "end": 1.0},
                    {"text": "world", "start": 1.0, "end": 2.0},
                ],
                "style": {
                    "bg_mode": "canva_fit",
                    "color_txt": "#FFFFFF",
                    "color_hl": "#FFFFFF",
                    "use_hl": True,
                    "size": 90,
                },
            },
            0.5,
            1080,
        )

        self.assertIn("color: #FFFFFF", html)
        self.assertNotIn("color: #FF3B30", html)

    def test_highlight_style_variants_render_distinct_effects(self):
        base = {
            "start": 0.0,
            "end": 2.0,
            "text": "hello world",
            "words": [
                {"text": "hello", "start": 0.0, "end": 1.0},
                {"text": "world", "start": 1.0, "end": 2.0},
            ],
            "style": {
                "bg_mode": "canva_fit",
                "color_hl": "#FF3B30",
                "hl_bg_color": "#FF0050",
                "use_hl": True,
                "size": 90,
            },
        }
        underline = copy.deepcopy(base)
        underline["style"]["hl_style"] = "underline"
        capsule = copy.deepcopy(base)
        capsule["style"]["hl_style"] = "capsule"
        glow = copy.deepcopy(base)
        glow["style"]["hl_style"] = "glow"
        frame = copy.deepcopy(base)
        frame["style"]["hl_style"] = "canva_frame"

        self.assertIn("background-position: 0 calc(100%", render_subtitle_html(underline, 0.5, 1080))
        self.assertIn("border-radius: 999px", render_subtitle_html(capsule, 0.5, 1080))
        self.assertIn("drop-shadow", render_subtitle_html(glow, 0.5, 1080))
        frame_html = render_subtitle_html(frame, 0.5, 1080)
        self.assertIn("box-shadow: 0 0 0", frame_html)
        self.assertIn("255, 0, 80", frame_html)

    def test_canva_fit_background_auto_scales_to_canvas_resolution(self):
        sub = {
            "start": 0.0,
            "end": 2.0,
            "text": "hello world",
            "words": [{"text": "hello world", "start": 0.0, "end": 2.0}],
            "style": {
                "bg_mode": "canva_fit",
                "bg_color": "#101010",
                "bg_alpha": 72,
                "bg_radius": 18,
                "bg_pad_left": 22,
                "bg_pad_right": 18,
                "bg_pad_top": 7,
                "bg_pad_bottom": 9,
                "box_width": 58,
                "box_layout": "auto",
                "size": 90,
            },
        }

        auto_html = render_subtitle_html(sub, 0.5, 2160, 3840)
        manual_sub = copy.deepcopy(sub)
        manual_sub["style"]["bg_auto_resolution"] = False
        manual_html = render_subtitle_html(manual_sub, 0.5, 2160, 3840)

        self.assertIn("padding: 0.6481vw 1.6667vw 0.8333vw 2.0370vw", auto_html)
        self.assertIn("padding: 0.3241vw 0.8333vw 0.4167vw 1.0185vw", manual_html)


class PreviewOverlaySyncTests(unittest.TestCase):
    def test_template_literal_escaping_handles_special_chars(self):
        self.assertEqual(escape_template_literal(r"a\b`c${x}"), r"a\\b\`c\${x}")

    def test_overlay_hash_is_stable_for_same_payload(self):
        left = stable_overlay_hash([{"idx": 1, "text": "hello"}], "<sig>", "<design>")
        right = stable_overlay_hash([{"text": "hello", "idx": 1}], "<sig>", "<design>")

        self.assertEqual(left, right)
        self.assertNotEqual(left, stable_overlay_hash([{"idx": 2, "text": "hello"}], "<sig>", "<design>"))

    def test_overlay_sync_script_escapes_all_channels(self):
        script = build_overlay_sync_script(
            [{"idx": 1, "htmlText": r"`\${sub}"}],
            r"<b>`\${sig}</b>",
            r"<i>`\${design}</i>",
        )

        self.assertIn("syncDesign", script)
        self.assertIn("syncSubs", script)
        self.assertIn("syncSignature", script)
        self.assertIn(r"\`", script)
        self.assertIn(r"\\", script)
        self.assertIn(r"\$", script)


class PreviewOverlayVisibilityTests(unittest.TestCase):
    def test_overlay_has_content_checks_all_channels(self):
        self.assertFalse(preview_overlay_visibility.overlay_has_content([], "", " "))
        self.assertTrue(preview_overlay_visibility.overlay_has_content([{"idx": 1}], "", ""))
        self.assertTrue(preview_overlay_visibility.overlay_has_content([], "<div></div>", ""))
        self.assertTrue(preview_overlay_visibility.overlay_has_content([], "", "<b></b>"))

    def test_visibility_actions_show_hide_and_disabled_overlay(self):
        actions = preview_overlay_visibility.overlay_visibility_actions(
            wants_overlay=True,
            overlay_enabled=True,
            browser_visible=False,
            previous_wants_overlay=False,
        )
        self.assertIn(preview_overlay_visibility.ACTION_SHOW_VIDEO, actions)
        self.assertIn(preview_overlay_visibility.ACTION_SHOW_OVERLAY, actions)
        self.assertIn(preview_overlay_visibility.ACTION_RAISE_OVERLAY, actions)

        disabled = preview_overlay_visibility.overlay_visibility_actions(
            wants_overlay=True,
            overlay_enabled=False,
            browser_visible=True,
            previous_wants_overlay=True,
        )
        self.assertIn(preview_overlay_visibility.ACTION_HIDE_OVERLAY, disabled)
        self.assertIn(preview_overlay_visibility.ACTION_RAISE_VIDEO, disabled)

    def test_should_sync_overlay_visibility_catches_stale_visible_browser(self):
        self.assertTrue(preview_overlay_visibility.should_sync_overlay_visibility(True, False, False))
        self.assertTrue(preview_overlay_visibility.should_sync_overlay_visibility(False, False, True))
        self.assertFalse(preview_overlay_visibility.should_sync_overlay_visibility(True, True, True))


class PreviewOverlayPayloadTests(unittest.TestCase):
    def test_build_preview_overlay_payload_combines_all_overlay_channels(self):
        subtitles = [
            {"start": 0.0, "end": 2.0, "text": "hello", "style": {"box_width": 200}},
            {"start": 3.0, "end": 4.0, "text": "later"},
        ]

        payload = build_preview_overlay_payload(
            subtitles,
            1.0,
            1080,
            1920,
            selected_idx=0,
            active_cache=set(),
            signature_config={"enabled": True, "text": "sig"},
            design_state={"pages": []},
            render_subtitle_html=lambda sub, time_sec, width, height: f"sub:{sub['text']}:{time_sec}:{width}x{height}",
            render_design_html=lambda design, time_sec, width, height: f"design:{width}x{height}",
            render_signature_html=lambda sig, time_sec, width, height: f"sig:{sig['text']}:{width}x{height}",
        )

        self.assertTrue(payload["has_content"])
        self.assertEqual(payload["active_indices"], {0})
        self.assertEqual(payload["active_subs"][0]["htmlText"], "sub:hello:1.0:1080x1920")
        self.assertIn("design:1080x1920", payload["design_html"])
        self.assertIn("sig:sig:1080x1920", payload["signature_html"])
        self.assertIn("syncSubs", payload["script"])
        self.assertEqual(
            payload["hash"],
            stable_overlay_hash(payload["active_subs"], payload["signature_html"], payload["design_html"]),
        )

    def test_build_preview_overlay_payload_can_be_empty(self):
        payload = build_preview_overlay_payload(
            [],
            10.0,
            1080,
            1920,
            selected_idx=-1,
            active_cache=set(),
            signature_config={},
            design_state={},
            render_subtitle_html=lambda *_args: "",
            render_design_html=lambda *_args: "",
            render_signature_html=lambda *_args: "",
        )

        self.assertFalse(payload["has_content"])
        self.assertEqual(payload["active_subs"], [])
        self.assertEqual(payload["active_indices"], set())


class JobControlTests(unittest.TestCase):
    def test_pause_toggle_and_cancel_are_cooperative(self):
        control = CooperativeJobControl("batch")

        self.assertTrue(control.toggle_pause())
        self.assertFalse(control.toggle_pause())
        self.assertTrue(control.request_cancel())
        self.assertTrue(control.cancel_requested)
        self.assertFalse(control.pause_requested)
        self.assertEqual(control.finish_reason, "cancelled")
        self.assertFalse(control.request_cancel())


class MediaProbeTests(unittest.TestCase):
    def test_rate_parsing_and_missing_fingerprint_are_safe(self):
        self.assertAlmostEqual(media_probe.parse_rate("30000/1001"), 29.970029, places=5)
        self.assertEqual(media_probe.parse_rate("0/0"), 0.0)
        self.assertEqual(media_probe.parse_rate("N/A"), 0.0)
        self.assertEqual(media_probe.media_fingerprint(os.path.join("missing", "clip.mp4")), ("", 0, 0))
        media_probe.clear_media_probe_cache()

    def test_timeline_duration_policy_prefers_substantially_shorter_audio(self):
        duration, info = media_duration_policy.choose_timeline_media_duration(
            exact=60.0,
            video=60.0,
            audio=40.0,
            packet=60.0,
        )

        self.assertEqual(duration, 40.0)
        self.assertEqual(info["reason"], "audio_shorter_than_container")

    def test_timeline_duration_policy_keeps_close_video_duration(self):
        duration, info = media_duration_policy.choose_timeline_media_duration(
            exact=60.0,
            video=60.0,
            audio=59.5,
            packet=60.0,
        )

        self.assertEqual(duration, 60.0)
        self.assertEqual(info["reason"], "video")

    def test_timeline_media_duration_skips_packet_scan_by_default(self):
        with mock.patch.object(media_probe, "get_exact_duration", return_value=60.0), \
                mock.patch.object(media_probe, "get_video_stream_duration", return_value=60.0), \
                mock.patch.object(media_probe, "has_audio_stream", return_value=False), \
                mock.patch.object(media_probe, "estimate_video_packet_duration", side_effect=AssertionError("packet scan should be opt-in")):
            duration, info = media_probe.get_timeline_media_duration("clip.mp4")

        self.assertEqual(duration, 60.0)
        self.assertEqual(info["reason"], "video")
        self.assertEqual(info["durations"]["packet"], 0.0)

    def test_precise_timeline_media_duration_uses_packet_scan(self):
        with mock.patch.object(media_probe, "get_exact_duration", return_value=60.0), \
                mock.patch.object(media_probe, "get_video_stream_duration", return_value=60.0), \
                mock.patch.object(media_probe, "has_audio_stream", return_value=False), \
                mock.patch.object(media_probe, "estimate_video_packet_duration", return_value=40.0) as packet_probe:
            duration, info = media_probe.get_timeline_media_duration("clip.mp4", precise=True)

        packet_probe.assert_called_once_with("clip.mp4")
        self.assertEqual(duration, 40.0)
        self.assertEqual(info["reason"], "packet_video_duration")


class SubtitleTimingTests(unittest.TestCase):
    def test_scripture_reference_quotes_are_normalized(self):
        raw = 'Jon 14 ： 6 "I am the way." extra'

        self.assertEqual(
            normalize_scripture_quote_text(raw),
            'Jon 14:6"I am the way." extra',
        )
        self.assertEqual(
            format_subtitle_text_spacing('Jon 14: 6" I am the way."'),
            'Jon 14:6"I am the way."',
        )

    def test_unquoted_scripture_line_is_wrapped(self):
        self.assertEqual(
            normalize_scripture_quote_text("136：1 Give thanks to the Lord."),
            '136:1"Give thanks to the Lord."',
        )

    def test_scripture_quote_token_stays_attached_to_first_word(self):
        tokens = tokenize_display_text('Jon 14:6 "I am the way."')

        self.assertIn('14:6"I', tokens)
        self.assertNotIn('6"', tokens)
        self.assertNotIn('I', tokens)

    def test_aligned_reference_text_preserves_scripture_quote_spacing(self):
        ai_words = [
            {"word": "Jon", "start": 0.0, "end": 0.1},
            {"word": "14", "start": 0.1, "end": 0.2},
            {"word": "6", "start": 0.2, "end": 0.3},
            {"word": "I", "start": 0.3, "end": 0.4},
            {"word": "am", "start": 0.4, "end": 0.5},
        ]

        aligned = align_reference_text_to_timestamps(ai_words, 'Jon 14:6 "I am the way."')
        words = [item["word"] for item in aligned]

        self.assertIn('14:6"I', words)
        self.assertNotIn('6"', words)

    def test_collapsed_ai_word_times_are_spread_over_media_duration(self):
        words = [
            {"word": "Inspired", "start": 13.40, "end": 13.41},
            {"word": "by", "start": 13.40, "end": 13.41},
            {"word": "Psalm", "start": 13.41, "end": 13.42},
            {"word": "121:7.", "start": 13.41, "end": 13.42},
            {"word": "Woman,", "start": 13.42, "end": 13.43},
            {"word": "believe", "start": 13.42, "end": 13.43},
            {"word": "that", "start": 13.43, "end": 13.44},
            {"word": "Amen.", "start": 13.43, "end": 13.44},
        ]

        repaired = normalize_word_timestamps(words, fallback_start=0.0, fallback_end=14.4)

        self.assertLess(repaired[0]["start"], 0.05)
        self.assertGreater(repaired[-1]["end"], 14.0)
        for prev, curr in zip(repaired, repaired[1:]):
            self.assertGreater(prev["end"], prev["start"])
            self.assertGreaterEqual(curr["start"], prev["end"] - 0.000001)

    def test_rebalance_pushes_overlapping_same_track_subtitles_apart(self):
        subs = [
            {"text": "first", "start": 13.40, "end": 13.45, "track": 1},
            {"text": "second", "start": 13.41, "end": 13.46, "track": 1},
            {"text": "third", "start": 13.42, "end": 13.47, "track": 1},
        ]

        balanced, stats = rebalance_subtitle_layout(subs, allow_split=False)

        self.assertEqual(stats["overlaps_fixed"], 2)
        for prev, curr in zip(balanced, balanced[1:]):
            self.assertGreaterEqual(prev["end"], prev["start"] + 0.18)
            self.assertGreaterEqual(curr["start"], prev["end"] + 0.009)

    def test_readability_break_guard_keeps_orphan_words_with_next_phrase(self):
        self.assertTrue(
            should_defer_subtitle_break_for_readability(
                "I",
                "will",
                segment_word_count=8,
                silence_gap=0.08,
                has_punct=False,
            )
        )
        self.assertFalse(
            should_defer_subtitle_break_for_readability(
                "I.",
                "will",
                segment_word_count=8,
                silence_gap=0.08,
                has_punct=True,
            )
        )

    def test_single_word_long_subtitle_segments_merge_with_neighbor(self):
        subs = [
            {
                "text": "God",
                "start": 0.0,
                "end": 0.4,
                "track": 1,
                "words": [{"text": "God", "start": 0.0, "end": 0.4}],
            },
            {
                "text": "sees your burden",
                "start": 0.4,
                "end": 1.8,
                "track": 1,
                "words": [
                    {"text": "sees", "start": 0.4, "end": 0.8},
                    {"text": "your", "start": 0.8, "end": 1.1},
                    {"text": "burden", "start": 1.1, "end": 1.8},
                ],
            },
        ]

        merged = merge_single_word_subtitle_segments(subs)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["text"], "God sees your burden")
        self.assertEqual(len(merged[0]["words"]), 4)


    def test_fast_subtitle_pacing_merges_tiny_segments(self):
        subs = [
            {"text": "God", "start": 0.0, "end": 0.06, "track": 1, "words": [{"text": "God", "start": 0.0, "end": 0.06}]},
            {"text": "sees", "start": 0.06, "end": 0.12, "track": 1, "words": [{"text": "sees", "start": 0.06, "end": 0.12}]},
            {"text": "you", "start": 0.12, "end": 0.18, "track": 1, "words": [{"text": "you", "start": 0.12, "end": 0.18}]},
        ]

        protected = protect_fast_subtitle_pacing(subs)

        self.assertEqual(len(protected), 1)
        self.assertEqual(protected[0]["text"], "God sees you")
        self.assertGreaterEqual(protected[0]["end"] - protected[0]["start"], 0.42)
        self.assertFalse(
            should_defer_subtitle_break_for_readability(
                "I",
                "will",
                segment_word_count=8,
                silence_gap=1.4,
                has_punct=False,
            )
        )
        self.assertTrue(
            should_defer_subtitle_break_for_readability(
                "God",
                "sees",
                segment_word_count=9,
                silence_gap=0.08,
                has_punct=False,
            )
        )
        self.assertFalse(
            should_defer_subtitle_break_for_readability(
                "God.",
                "sees",
                segment_word_count=9,
                silence_gap=0.08,
                has_punct=True,
            )
        )

    def test_fast_subtitle_pacing_preserves_precise_segments_without_merge(self):
        subs = [
            {"text": "God", "start": 0.0, "end": 0.06, "track": 1, "words": [{"text": "God", "start": 0.0, "end": 0.06}]},
            {"text": "sees", "start": 0.06, "end": 0.12, "track": 1, "words": [{"text": "sees", "start": 0.06, "end": 0.12}]},
            {"text": "you", "start": 0.12, "end": 0.18, "track": 1, "words": [{"text": "you", "start": 0.12, "end": 0.18}]},
        ]

        protected = protect_fast_subtitle_pacing(subs, allow_merge=False)

        self.assertEqual([item["text"] for item in protected], ["God", "sees", "you"])



if __name__ == "__main__":
    unittest.main()
