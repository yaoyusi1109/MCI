
from pathlib import Path
import osmnx as ox
import networkx as nx

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

GRAPH_PATH = DATA_DIR / "baltimore_direct.graphml"

# Enable caching to reduce repeated API requests
ox.settings.use_cache = True
ox.settings.log_console = True
ox.settings.timeout = 300

print("Downloading Baltimore City road network...")

G = ox.graph_from_place(
    "Baltimore, Maryland, USA",
    network_type="drive",
    simplify=True,
    retain_all=False
)

print("Adding estimated road speeds...")
G = ox.add_edge_speeds(G)

print("Adding travel times...")
G = ox.add_edge_travel_times(G)

print("Saving road network...")
ox.save_graphml(G, GRAPH_PATH)

print("\nBaltimore road network downloaded!")
print(f"Nodes: {G.number_of_nodes():,}")
print(f"Edges: {G.number_of_edges():,}")
print(f"Saved to: {GRAPH_PATH}")

print(
    "Weakly connected components:",
    nx.number_weakly_connected_components(G)
)
