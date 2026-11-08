
import osmnx as ox
import networkx as nx

G = ox.load_graphml(
    "data/baltimore_direct.graphml"
)

# Camden Yards
mci = (39.2839, -76.6217)

# Shock Trauma
hospital = (39.2885, -76.6235)

start = ox.distance.nearest_nodes(
    G, X=mci[1], Y=mci[0]
)

end = ox.distance.nearest_nodes(
    G, X=hospital[1], Y=hospital[0]
)

route = nx.shortest_path(
    G, start, end,
    weight="travel_time"
)

seconds = 0
meters = 0

for u, v in zip(route[:-1], route[1:]):
    edge = min(
        G[u][v].values(),
        key=lambda e: float(e["travel_time"])
    )
    seconds += float(edge["travel_time"])
    meters += float(edge["length"])

print("Direct OSMnx results:")
print(f"Travel time: {seconds / 60:.3f} minutes")
print(f"Distance: {meters / 1609.344:.3f} miles")
print(f"Road nodes: {len(route)}")
print(f"Directed path exists: {nx.has_path(G, start, end)}")
