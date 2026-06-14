# Professionalization Progress

## Current Estimate

The project is roughly 35-45% through the first professionalization phase.

The product workflow exists, and several infrastructure modules now exist, but many rooms still need to be wired into the shared controllers instead of keeping local state machines.

## Phase 1: Stability Kernel

Status: In progress.

- PreviewController: started. Preview timing, seek, loop, proxy, and overlay support have been split into helper modules, but `room_edit.py` still owns too much UI and playback coordination.
- MediaProbe: started. Duration, stream, and resolution probing now has a shared module path, but all rooms need to consistently call it.
- JobController: started. Pause/cancel/progress primitives exist, but batch creation, export, AI transcription, and preview proxy generation are not fully unified yet.
- AppStorage: in progress. Style presets, signature presets, settings, batch queue backups, export queue backups, and edit project cache now resolve through AppData/config/state/cache with one-time migration from the legacy program root or legacy temp cache. Shared JSON helpers now read safely and save atomically through a temporary file before replace. A few room-specific cache folders still need wiring.
- ThemeTokens: in progress. The shared theme token bridge is now wired into Edit, Batch, Deliver, and Settings, including common role styling for save/apply/export/pause/cancel buttons. Room-specific inline styles still need gradual migration.

## Phase 2: Architecture Split

Status: early.

- `room_edit.py` remains the largest risk and should be split gradually.
- `ui_components.py` still contains renderer and layout responsibilities that should move into focused renderer modules.
- Batch and delivery still need a fuller shared render pipeline and dry-run command model. FFmpeg concat entry generation is now shared so quoted/Windows paths are escaped consistently in batch and delivery.

## Phase 3: QA

Status: early.

- Core tests exist and new focused tests have been added for timeline/board/theme/storage utilities.
- Still needed: GUI smoke tests, preview state tests, export dry-run tests, and media fixtures.

## Phase 4: Product Polish

Status: in progress.

- Media pool has started moving toward a professional pool: isolated panel, preview area, compact list, grid mode, drag-to-timeline, and selection state.
- Design components are separated from media pool and organized into create/layer/property tabs.
- Engineering UI foundations for project board selection, sidebar collapse, and global visual polish are now partly wired into `room_project.py`: the project sidebar starts collapsed by default, remembers its state, Shift range selection uses a shared interaction helper, and grid refresh avoids an extra recursive scan during search.

## Phase 5: Release Professionalization

Status: early.

- License and font compliance work exists.
- Still needed: installer/update path, crash logging, user data migration wiring, and commercial license decision for PyQt/FFmpeg.

## Next Best Steps

1. Move remaining room-specific cache folders through `app_storage.py`.
2. Continue migrating room-specific inline styles onto `theme_tokens.py`.
3. Wire `project_board_interactions.py`, `project_sidebar_state.py`, and `project_ui_kit.py` into `room_project.py`.
4. Continue shrinking `room_edit.py` by extracting media pool and design panel construction into dedicated modules.
5. Add export dry-run and preview state tests before deeper render pipeline refactors.
