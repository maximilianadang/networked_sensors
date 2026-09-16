#pragma once
// Host tests exercise deterministic handoffs, not actual interrupt exclusion.
#define ATOMIC_RESTORESTATE 0
#define ATOMIC_BLOCK(mode) for (bool atomicOnce = true; atomicOnce; atomicOnce = false)
