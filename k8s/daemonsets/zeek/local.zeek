@load policy/tuning/json-logs

redef LogAscii::use_json = T;
redef LogAscii::json_timestamps = JSON::TS_EPOCH;
redef Log::default_rotation_interval = 1hr;
redef Log::default_logdir = "/zeek-logs";

# K3s flannel uses the Linux VXLAN port, not the IANA 4789 Zeek registers by default.
# Decoding it logs inner pod connections; the outer node-to-node flows get service "vxlan".
redef PacketAnalyzer::VXLAN::vxlan_ports += { 8472/udp };
redef likely_server_ports += { 8472/udp };

# Capture whole packets, like tcpdump -s 0. The default 9216-byte snaplen truncates this host's own
# offloaded (TSO/GSO) TCP segments, e.g. kube-apiserver responses up to ~17 KB. Zeek then logs them as
# content gaps (missed_bytes, history "g") that offline pcap replays, and the nids training data, never have.
redef Pcap::snaplen = 262144;
