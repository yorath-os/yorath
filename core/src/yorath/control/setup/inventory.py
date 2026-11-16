"""Node inventory (decision 23): one ``local`` row in the ``local`` cluster, set by bootstrap."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.cluster.placements import ensure_servers
from yorath.control.model import Cluster, Node
from yorath.control.setup.hardware import HardwareInfo
from yorath.db import utcnow


async def upsert_node(session: AsyncSession, name: str, hw: HardwareInfo) -> Node:
    cluster = await session.scalar(select(Cluster).where(Cluster.name == "local"))
    if cluster is None:
        cluster = Cluster(name="local")
        session.add(cluster)
        await session.flush()
    node = await session.scalar(select(Node).where(Node.name == name))
    if node is None:
        node = Node(cluster_id=cluster.id, name=name, cpu_count=hw.vcpu, ram_gb=hw.ram_gb)
        session.add(node)
    node.cpu_count, node.ram_gb = hw.vcpu, hw.ram_gb
    node.gpu, node.vram_gb, node.gpu_count = hw.gpu_name, hw.vram_gb, hw.gpu_count
    node.updated_at = utcnow()
    await session.flush()
    await ensure_servers(session, node)  # the main node's Ollama is its model server
    await session.commit()
    return node
