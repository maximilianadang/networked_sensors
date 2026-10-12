#pragma once
#define ATOMIC_RESTORESTATE 0
#define ATOMIC_BLOCK(type) for (bool atomicOnce = true; atomicOnce; atomicOnce = false)
