"""
CPU, RAM and swap parsing.

This code only runs on Linux — it reads /proc and cgroup, neither of which
exists on macOS — so on a developer machine it returns almost nothing and looks
fine whether or not it is correct. These tests feed it the real file formats
from both cgroup versions so the parsing is checked where it cannot otherwise
be observed.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_res.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

# Real /proc/meminfo, trimmed. Numbers are kB and must come back as bytes.
MEMINFO = """MemTotal:        3906892 kB
MemFree:          198372 kB
MemAvailable:    1204416 kB
Buffers:           84120 kB
SwapTotal:       4194300 kB
SwapFree:        3010556 kB
"""


def _parse_meminfo(text: str) -> dict:
    """The same kB → bytes parse the reader does, for building fixtures."""
    out = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            out[key] = int(parts[0]) * 1024
    return out


def _patch(monkeypatch, files: dict, meminfo: str = None):
    """Serve fake cgroup and meminfo data to the reader.

    Both are stubbed, always. An earlier version patched only _read_first and
    let _read_meminfo fall through to the real file — which is nothing on macOS
    and a populated one on Linux, so the tests passed locally and failed in CI
    against the runner's own memory. A test about parsing must not depend on
    the machine it runs on.
    """
    from app.api.routes import monitoring as mon

    def fake_read_first(*paths):
        for p in paths:
            if p in files:
                return files[p]
        return None
    monkeypatch.setattr(mon, "_read_first", fake_read_first)
    monkeypatch.setattr(mon, "_read_meminfo",
                        lambda: _parse_meminfo(meminfo) if meminfo else {})
    return mon


class TestCgroupV2:
    def test_memory_and_cpu(self, monkeypatch):
        mon = _patch(monkeypatch, {
            "/sys/fs/cgroup/memory.current": "536870912",       # 512 MiB
            "/sys/fs/cgroup/memory.max": "1073741824",          # 1 GiB
            "/sys/fs/cgroup/cpu.stat": "usage_usec 12345678\nuser_usec 900\n",
            "/sys/fs/cgroup/cpu.max": "150000 100000",          # 1.5 cores
            "/proc/loadavg": "0.52 0.41 0.38 2/310 1234",
        })
        r = mon._read_resources()
        assert r["memory_used_bytes"] == 536870912
        assert r["memory_limit_bytes"] == 1073741824
        assert r["cpu_usage_usec"] == 12345678
        assert r["cpu_limit_cores"] == 1.5
        assert r["load_1m"] == 0.52 and r["load_15m"] == 0.38

    def test_unlimited_memory_reads_as_no_limit(self, monkeypatch):
        """cgroup v2 spells "no limit" as the literal string max."""
        mon = _patch(monkeypatch, {
            "/sys/fs/cgroup/memory.current": "1000",
            "/sys/fs/cgroup/memory.max": "max",
        })
        assert mon._read_resources().get("memory_limit_bytes") is None

    def test_unlimited_cgroup_falls_back_to_the_host_total(self, monkeypatch):
        """This is the path production actually takes: k8s/04-backend.yaml sets
        no memory limit, so the cgroup says "max" and the host total is the
        honest ceiling to show a percentage against."""
        mon = _patch(monkeypatch, {
            "/sys/fs/cgroup/memory.current": "1000",
            "/sys/fs/cgroup/memory.max": "max",
        }, meminfo=MEMINFO)
        r = mon._read_resources()
        assert r["memory_limit_bytes"] == 3906892 * 1024
        # and the cgroup's own usage still wins over the host figure, because
        # it is this container's number
        assert r["memory_used_bytes"] == 1000

    def test_unlimited_cpu_is_not_reported_as_a_quota(self, monkeypatch):
        mon = _patch(monkeypatch, {"/sys/fs/cgroup/cpu.max": "max 100000"})
        assert "cpu_limit_cores" not in mon._read_resources()


class TestCgroupV1:
    def test_memory_and_cpu(self, monkeypatch):
        mon = _patch(monkeypatch, {
            "/sys/fs/cgroup/memory/memory.usage_in_bytes": "268435456",
            "/sys/fs/cgroup/memory/memory.limit_in_bytes": "536870912",
            # v1 reports nanoseconds; we normalise to microseconds
            "/sys/fs/cgroup/cpuacct/cpuacct.usage": "9876543210",
        })
        r = mon._read_resources()
        assert r["memory_used_bytes"] == 268435456
        assert r["memory_limit_bytes"] == 536870912
        assert r["cpu_usage_usec"] == 9876543
    def test_v1_no_limit_sentinel_is_treated_as_unlimited(self, monkeypatch):
        """v1 spells "no limit" as a number near 2^63, which would otherwise be
        rendered as an 8-exabyte memory limit."""
        mon = _patch(monkeypatch, {
            "/sys/fs/cgroup/memory/memory.usage_in_bytes": "1000",
            "/sys/fs/cgroup/memory/memory.limit_in_bytes": str(9223372036854771712),
        })
        assert mon._read_resources().get("memory_limit_bytes") is None


class TestSwap:
    def test_swap_is_read_from_meminfo(self, monkeypatch):
        """bootstrap.sh adds 4GB of swap because RAM is tight, so swap filling
        is a real signal rather than a curiosity."""
        mon = _patch(monkeypatch, {}, meminfo=MEMINFO)
        r = mon._read_resources()
        assert r["swap_total_bytes"] == 4194300 * 1024
        assert r["swap_used_bytes"] == (4194300 - 3010556) * 1024
        assert r["swap_used_percent"] == pytest.approx(28.2, abs=0.2)

    def test_host_memory_used_excludes_cache(self, monkeypatch):
        """MemAvailable, not MemFree — page cache is not pressure."""
        mon = _patch(monkeypatch, {}, meminfo=MEMINFO)
        r = mon._read_resources()
        assert r["host_memory_total_bytes"] == 3906892 * 1024
        assert r["host_memory_used_bytes"] == (3906892 - 1204416) * 1024

    def test_kilobytes_are_converted_to_bytes(self, monkeypatch):
        """meminfo is kB; reporting it raw would understate memory 1024x."""
        mon = _patch(monkeypatch, {}, meminfo=MEMINFO)
        assert mon._read_resources()["swap_total_bytes"] > 4_000_000_000


class TestWhenNothingIsReadable:
    def test_missing_files_yield_no_keys_rather_than_raising(self, monkeypatch):
        """None of this exists on macOS; a developer should see empty tiles,
        not a stack trace."""
        mon = _patch(monkeypatch, {})                   # no cgroup, no meminfo
        r = mon._read_resources()
        assert isinstance(r, dict)
        assert r.get("cpu_count")                       # os.cpu_count always works
        for absent in ("cpu_usage_usec", "swap_total_bytes", "memory_limit_bytes"):
            assert absent not in r or r[absent] is None

    def test_it_runs_against_the_real_machine_without_raising(self):
        """Unpatched, on whatever this is. Proves the reader survives both a
        Linux runner with real cgroup files and a mac with none."""
        from app.api.routes import monitoring as mon
        r = mon._read_resources()
        assert isinstance(r, dict) and r.get("cpu_count")


class TestReachability:
    def test_the_probe_is_cached(self):
        """The live view polls every 5s. An outbound request at that rate is
        load on someone else's server and looks like scanning from ours."""
        from app.api.routes import monitoring as mon
        assert mon._REACH_TTL >= 30, "reachability cache is too short for a 5s poll"

    def test_only_divar_is_probed(self):
        """Divar is the scraper's lifeline and can go unreachable from an
        Iranian network without anything here changing. Probing Google would
        fail permanently under sanctions, and a always-red tile teaches people
        to ignore the panel."""
        import inspect
        from app.api.routes import monitoring as mon
        src = inspect.getsource(mon._check_reachability)
        assert "divar.ir" in src
        for host in ("google.com", "googleapis.com", "cloudflare"):
            assert host not in src, f"probing {host} would fail permanently from Iran"

    def test_the_probe_cannot_break_the_page(self):
        """A health check that raises turns a warning into an outage."""
        import inspect
        from app.api.routes import monitoring as mon
        src = inspect.getsource(mon._check_reachability)
        assert "except Exception" in src and "timeout" in src
