SERVER_PORT = 5000

# --- GAME WINDOW ---
# Toggle automatic activation of PoE window before actions.
ACTIVATE_GAME_WINDOW = True
# Delay after window restore/focus to let the game repaint.
GAME_WINDOW_ACTIVATION_DELAY = 0.35

# --- READY TO BUY DETECTOR ---
# Interval between inventory patch checks (s).
# Required consecutive matches to trigger READY_TO_BUY.
READY_CONSECUTIVE_MATCHES = 3
# Patch matching thresholds for READY detector.
READY_MSE_THRESHOLD = 200.0
READY_CORR_THRESHOLD = 0.5
# Timeout waiting for READY_TO_BUY after teleport (s).
READY_WAIT_TIMEOUT = 10.0

# --- AUTOBUY ---
# Interval between CTRL+LMB clicks within one attempt.
AUTOBUY_CLICK_INTERVAL = 0.1
# Pause after moving cursor away to inventory.
AUTOBUY_MOVE_WAIT = 0.2
# Merchant patch size relative to cell (0..1, centered).
MERCHANT_PATCH_SCALE = 0.5
# Thresholds for merchant patch matching.
MERCHANT_PATCH_MSE_THRESHOLD = 200.0
MERCHANT_PATCH_CORR_THRESHOLD = 0.5

# --- ONLY TP ---
# Hold duration after teleport in ONLY_TP mode (s).
TP_ONLY_HOLD_SECONDS = 30

# --- CHAT / HIDEOUT ---
# Delay between chat actions when sending /hideout (s).
HIDEOUT_CHAT_DELAY = 0.2
