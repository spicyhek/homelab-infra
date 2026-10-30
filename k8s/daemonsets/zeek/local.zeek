@load policy/tuning/json-logs

redef LogAscii::use_json = T;
redef LogAscii::json_timestamps = JSON::TS_EPOCH;
redef Log::default_rotation_interval = 1hr;
redef Log::default_logdir = "/zeek-logs";

# K3s flannel uses the Linux VXLAN port, not the IANA 4789 Zeek registers by default.
# Decoding it logs inner pod connections; the outer node-to-node flows get service "vxlan".
redef PacketAnalyzer::VXLAN::vxlan_ports += { 8472/udp };
redef likely_server_ports += { 8472/udp };
