"""Hot-path shortcuts: in-memory rate limit and shared public responses."""

from app import fastpath


def test_memory_rate_limit_counts_per_window():
    fastpath.clear()
    results = [fastpath.memory_rate_limit("k", 3, 60)[0] for _ in range(5)]
    assert results == [True, True, True, False, False]
    assert fastpath.memory_rate_limit("other", 3, 60)[0] is True


def test_only_public_market_paths_are_shared():
    assert fastpath.shared_ttl("/api/assets") == 2.0
    assert fastpath.shared_ttl("/api/assets/5") == 2.0
    assert fastpath.shared_ttl("/api/assets/5/live") == 1.0
    assert fastpath.shared_ttl("/api/assets/5/history") == 2.0
    for private in ("/api/account", "/api/orders", "/api/auth/me", "/api/admin/channels", "/api/assets/5/unknown",
                    "/api/notifications", "/api/missions"):
        assert fastpath.shared_ttl(private) is None, private


def test_shared_cache_expires():
    fastpath.clear()
    fastpath.cache_put("a", 0.0, 200, b"x", "application/json")
    assert fastpath.cache_get("a") is None
    fastpath.cache_put("b", 5.0, 200, b"y", "application/json")
    assert fastpath.cache_get("b") == (200, b"y", "application/json")



def test_watched_assets_are_tracked_from_requests():
    fastpath.clear()
    fastpath._views.clear()
    fastpath.mark_view("/api/assets/7/live")
    fastpath.mark_view("/api/assets")
    fastpath.mark_view("/api/account")
    assert fastpath.watched_assets() == [7]
