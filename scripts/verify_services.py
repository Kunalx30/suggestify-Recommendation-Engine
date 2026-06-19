"""
verify_services.py — Suggestify V2
====================================
Run after `docker compose up -d` to verify all 8 services are healthy.

Usage:
    python scripts/verify_services.py
"""

import asyncio
import os
import sys
import socket
import time

import httpx
import asyncpg
import redis as redis_sync
from rich.console import Console
from rich.table import Table
from dotenv import load_dotenv

load_dotenv()
console = Console()

CHECKS = [
    {
        "name": "PostgreSQL",
        "port": 5433,
        "icon": "🐘",
    },
    {
        "name": "Redis",
        "port": 6379,
        "icon": "⚡",
    },
    {
        "name": "Qdrant",
        "port": 6333,
        "icon": "🔍",
        "http": "http://localhost:6333/readyz",
    },
    {
        "name": "Zookeeper",
        "port": 2181,
        "icon": "🦁",
    },
    {
        "name": "Kafka",
        "port": 9092,
        "icon": "📨",
    },
    {
        "name": "Prometheus",
        "port": 9090,
        "icon": "📊",
        "http": "http://localhost:9090/-/healthy",
    },
    {
        "name": "Grafana",
        "port": 3001,
        "icon": "📈",
        "http": "http://localhost:3001/api/health",
    },
    {
        "name": "MLflow",
        "port": 5000,
        "icon": "🧪",
        "http": "http://localhost:5000/health",
    },
]


def check_port(host: str, port: int, timeout: float = 3.0) -> bool:
    """Check if a TCP port is open."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


async def check_postgres() -> tuple[bool, str]:
    try:
        conn = await asyncpg.connect(
            os.getenv("DATABASE_URL"),
            timeout=5
        )
        version = await conn.fetchval("SELECT version()")
        count = await conn.fetchval("SELECT COUNT(*) FROM items")
        await conn.close()
        return True, f"✅ Connected | Items in DB: {count:,}"
    except Exception as e:
        return False, f"✗ {str(e)[:60]}"


def check_redis() -> tuple[bool, str]:
    try:
        r = redis_sync.Redis(host="localhost", port=6379, socket_timeout=3)
        r.ping()
        info = r.info("memory")
        mem_mb = info.get("used_memory_human", "?")
        return True, f"✅ Connected | Memory used: {mem_mb}"
    except Exception as e:
        return False, f"✗ {str(e)[:60]}"


async def check_http(url: str, name: str) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(url)
            if r.status_code < 400:
                return True, f"✅ HTTP {r.status_code}"
            return False, f"✗ HTTP {r.status_code}"
    except Exception as e:
        return False, f"✗ {str(e)[:60]}"


async def run_checks():
    console.rule("[bold cyan]🔍 Suggestify V2 — Service Health Check[/bold cyan]")

    results = []

    for check in CHECKS:
        name = check["name"]
        icon = check["icon"]
        port = check["port"]

        port_ok = check_port("localhost", port)

        detail = ""
        status = port_ok

        if name == "PostgreSQL" and port_ok:
            status, detail = await check_postgres()
        elif name == "Redis" and port_ok:
            status, detail = check_redis()
        elif "http" in check and port_ok:
            status, detail = await check_http(check["http"], name)
        elif not port_ok:
            detail = f"✗ Port {port} not reachable"

        results.append((icon, name, port, status, detail or ("✅ Port open" if port_ok else "")))

    table = Table(show_header=True, header_style="bold magenta", title="Service Status")
    table.add_column("", width=3)
    table.add_column("Service", style="cyan")
    table.add_column("Port", justify="right")
    table.add_column("Status", justify="center")
    table.add_column("Details")

    all_ok = True
    for icon, name, port, status, detail in results:
        status_str = "[bold green]UP[/bold green]" if status else "[bold red]DOWN[/bold red]"
        if not status:
            all_ok = False
        table.add_row(icon, name, str(port), status_str, detail)

    console.print(table)

    if all_ok:
        console.print("\n[bold green]🎉 All 8 services are healthy! Ready for Day 1.[/bold green]")
        console.print("\nNext step: [bold]python scripts/load_data.py[/bold]")
    else:
        down = [r[1] for r in results if not r[3]]
        console.print(f"\n[bold red]⚠  {len(down)} service(s) are down: {', '.join(down)}[/bold red]")
        console.print("\nFix: [bold]docker compose up -d[/bold]")
        console.print("Logs: [bold]docker compose logs <service_name>[/bold]")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_checks())
