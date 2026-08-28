"""Named procedures for getting one kind of thing done in one kind of app.

Phase 4. A skill is not a prompt and not a plan the model writes: it is a
procedure with a name, the slots it cannot run without, ordered steps, and
a test for whether it worked. The registry says which skill serves which
goal, so "what can she actually do?" has an answer that can be read off
the code rather than guessed from the planner's branches.

Adding an ability is therefore a known amount of work: declare the goal
kind it serves, write the steps against the app's live tree, and ship the
live scenario that proves it.
"""

from brain.skills.media import (
    MediaSurface,
    PlayCollectionSkill,
    PlayTrackSkill,
    SkillResult,
    live_window_titles,
    playback_evidence,
    skill_for,
)

__all__ = [
    "MediaSurface",
    "PlayCollectionSkill",
    "PlayTrackSkill",
    "SkillResult",
    "live_window_titles",
    "playback_evidence",
    "skill_for",
]
