/**
 * Every proctoring threshold in one place, each with its reason.
 *
 * IMPORTANT: these are starting values chosen from geometry and the pass bar in
 * DEV_PLAN.md section 7, NOT values tuned on real recordings. None of them has
 * been checked against a real person in front of a real camera yet. Recorded
 * clips (track_i_notes/B_checks/videos/) and the tape-measure check on the dev
 * route are how they get tuned. Change them here only, and re-run the tests.
 */

// -- Detection rate ------------------------------------------------------------------

/** Frames analysed per second during the call. Behaviour changes over seconds, not milliseconds. */
export const DETECT_FPS = 8;

// -- Distance --------------------------------------------------------------------------

/** Average human iris diameter in millimetres (DEV_PLAN section 7; the population spread is about +-0.5 mm). */
export const IRIS_DIAMETER_MM = 11.7;

/**
 * Assumed horizontal field of view of the webcam, in degrees. Browsers do not
 * expose the focal length, so it is derived from this: f = (width / 2) /
 * tan(hfov / 2). Laptop and desk webcams sit roughly between 55 and 78 degrees
 * horizontally, which alone gives about +-21 % error at 65 (B_architecture.md).
 */
export const ASSUMED_HFOV_DEG = 65;

/** The target band (decision 9). */
export const BAND_MIN_CM = 50;
export const BAND_MAX_CM = 80;

/**
 * Margin outside the band before "out of band" starts, so a reading wobbling on
 * the boundary does not start and stop an event every second. Coming back
 * inside the band itself ends it.
 */
export const BAND_ENTER_MARGIN_CM = 2;

/** Out of band for this long before it counts. Leaning in to read something for a second is not an event. */
export const DISTANCE_SUSTAIN_S = 4;

/** Median over this window smooths per-frame iris noise. */
export const DISTANCE_SMOOTH_S = 1;

// -- Gaze ---------------------------------------------------------------------------------

/**
 * Away from the warm-up baseline by more than any of these counts as looking
 * away for that frame. Head turns past 25 degrees take the eyes off a laptop
 * screen at 60 cm (its half-width is about 16 degrees there). Eye offsets are a
 * fraction of the eye's corner-to-corner width; 0.14 is roughly 20 degrees of
 * eye rotation for a 12 mm eyeball radius and a 30 mm eye width.
 */
export const GAZE_HEAD_YAW_DEG = 25;
export const GAZE_HEAD_PITCH_DEG = 20;
export const GAZE_EYE_H = 0.14;
export const GAZE_EYE_V = 0.12;

/** A glance that returns within this long does not end the away episode (blinks, landmark jitter). */
export const GAZE_RETURN_GRACE_S = 0.6;

/**
 * Sustained: one look away of at least this long. The pass bar is a 10-second
 * phone glance; thinking glances are usually 1-4 seconds.
 */
export const GAZE_SUSTAINED_S = 6;

/** Repeated: this many looks of at least GAZE_GLANCE_MIN_S inside GAZE_REPEAT_WINDOW_S. */
export const GAZE_REPEAT_COUNT = 5;
export const GAZE_GLANCE_MIN_S = 1.5;
export const GAZE_REPEAT_WINDOW_S = 60;

/** Warm-up length (task 13) and the share of its frames that must show one face for a usable baseline. */
export const WARMUP_SECONDS = 30;
export const WARMUP_MIN_FACE_SHARE = 0.4;

// -- Faces -----------------------------------------------------------------------------------

/** No face for this long counts. A turn of the head or a sneeze is shorter. */
export const FACE_MISSING_S = 3;
/** Two faces for this long counts. Someone walking behind the candidate is shorter. */
export const SECOND_FACE_S = 2;
/** A frame or two of the other count inside a run does not end it. */
export const FACE_GRACE_S = 0.5;

// -- Browser signals ---------------------------------------------------------------------------

/** The window must stay out of focus this long. Clicking the browser's own share bar is shorter. */
export const UNFOCUSED_S = 2;
/** Pastes within this long of the last one are the same event (repeats + 1). */
export const PASTE_MERGE_S = 10;
/** How often the second-monitor state is re-read, as well as the screen "change" event. */
export const MONITOR_POLL_MS = 5000;

// -- Reminders (task 16) -------------------------------------------------------------------------

export const REMINDERS_PER_KIND = 3;
export const REMINDER_COOLDOWN_S = 120;
/** How long one toast stays on screen. */
export const TOAST_SECONDS = 6;

// -- Lighting (device check) ------------------------------------------------------------------------

/** Mean luma (0-255) of the face region. Below this the face is in shadow. */
export const LIGHT_FACE_MIN = 70;
/** Above this the face is washed out. */
export const LIGHT_FACE_MAX = 235;
/** Bright light behind the candidate: the frame is this much brighter than the face. */
export const LIGHT_BACKLIT_RATIO = 1.6;

// -- Hands (device check) -----------------------------------------------------------------------------

/** Two hands in this many recent frames passes "show both hands". */
export const HANDS_FRAMES_NEEDED = 3;
/** One try at the hands check lasts this long once the hand model is running (PA-02). */
export const HANDS_TRY_MS = 20_000;
/** After this many tries without both hands seen, the candidate may continue without it. */
export const HANDS_TRIES_BEFORE_CONTINUE = 2;
