"""
Unit tests for Distributed MongoDB Sync Lock.
"""
from backend.services.sync_lock import (
    acquire_sync_lock,
    is_sync_locked,
    release_sync_lock,
)


def test_distributed_sync_lock_acquisition_and_release():
    owner1 = "worker-process-1"
    owner2 = "worker-process-2"

    # Clean initial state
    release_sync_lock(owner=owner1)
    release_sync_lock(owner=owner2)

    # 1. Owner 1 acquires lock
    assert acquire_sync_lock(owner=owner1, ttl_seconds=60) is True
    assert is_sync_locked() is True

    # 2. Owner 2 tries to acquire while locked -> returns False
    assert acquire_sync_lock(owner=owner2, ttl_seconds=60) is False

    # 3. Owner 1 releases lock
    assert release_sync_lock(owner=owner1) is True
    assert is_sync_locked() is False

    # 4. Owner 2 can now acquire lock
    assert acquire_sync_lock(owner=owner2, ttl_seconds=60) is True
    assert is_sync_locked() is True

    # Clean up
    release_sync_lock(owner=owner2)


def test_distributed_sync_lock_ttl_expiration():
    """Verify that an expired lock allows automatic re-acquisition."""
    owner1 = "crashed-worker"
    owner2 = "new-worker"

    # Acquire lock with 0 TTL (already expired)
    acquire_sync_lock(owner=owner1, ttl_seconds=-1)

    # Owner 2 should be able to acquire immediately because owner1's lock expired
    assert acquire_sync_lock(owner=owner2, ttl_seconds=60) is True

    # Clean up
    release_sync_lock(owner=owner2)
