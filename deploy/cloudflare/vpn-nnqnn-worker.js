const SOURCE_URL = "https://raw.githubusercontent.com/zieng2/wl/main/vless_universal.txt";

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const maxNodes = Number(url.searchParams.get("max") || "300");

    const res = await fetch(SOURCE_URL, {
      headers: {
        "User-Agent": "Happ-Xray-Balancer"
      },
      cache: "no-store"
    });

    if (!res.ok) {
      return new Response("Cannot fetch source subscription", { status: 502 });
    }

    const text = await res.text();
    const lines = text
      .split(/\r?\n/)
      .map(x => x.trim())
      .filter(Boolean);

    const outbounds = [];
    let index = 1;

    for (const line of lines) {
      if (!line.startsWith("vless://")) continue;

      const outbound = vlessToOutbound(line, index);
      if (!outbound) continue;

      outbounds.push(outbound);
      index++;

      if (outbounds.length >= maxNodes) break;
    }

    if (outbounds.length === 0) {
      return new Response("No non-RU VLESS nodes found", { status: 500 });
    }

    const config = {
      log: {
        loglevel: "warning"
      },

      dns: {
        servers: [
          "https://dns.google/dns-query",
          "https://cloudflare-dns.com/dns-query"
        ],
        enableParallelQuery: true
      },

      inbounds: [
        {
          tag: "socks",
          port: 10808,
          listen: "127.0.0.1",
          protocol: "socks",
          settings: {
            udp: true,
            auth: "noauth"
          },
          sniffing: {
            enabled: true,
            routeOnly: true,
            destOverride: ["http", "tls", "quic"]
          }
        },
        {
          tag: "http",
          port: 10809,
          listen: "127.0.0.1",
          protocol: "http",
          settings: {
            allowTransparent: false
          },
          sniffing: {
            enabled: true,
            routeOnly: true,
            destOverride: ["http", "tls", "quic"]
          }
        }
      ],

      outbounds: [
        ...outbounds,
        {
          tag: "direct",
          protocol: "freedom"
        },
        {
          tag: "block",
          protocol: "blackhole"
        }
      ],

      routing: {
        domainMatcher: "hybrid",
        domainStrategy: "IPIfNonMatch",
        rules: [
          {
            ip: ["geoip:private"],
            outboundTag: "direct"
          },
          {
            domain: ["geosite:category-ads-all"],
            outboundTag: "block"
          },
          {
            protocol: ["bittorrent"],
            outboundTag: "direct"
          },
          {
            network: "tcp,udp",
            balancerTag: "auto"
          }
        ],
        balancers: [
          {
            tag: "auto",
            selector: ["auto-"],
            fallbackTag: "block",
            strategy: {
              type: "leastPing"
            }
          }
        ]
      },

      observatory: {
        subjectSelector: ["auto-"],
        probeUrl: "https://www.gstatic.com/generate_204",
        probeInterval: "1m",
        enableConcurrency: true
      },

      remarks: "kVPN @kkVPNrobot"
    };

    return new Response(JSON.stringify(config, null, 2), {
      headers: {
        "content-type": "application/json; charset=utf-8",
        "profile-title": "kVPN @kkVPNrobot",
        "subscription-auto-update-enable": "1"
      }
    });
  }
};

function vlessToOutbound(link, index) {
  let u;

  try {
    u = new URL(link);
  } catch {
    return null;
  }

  const q = u.searchParams;

  const name = safeDecode(u.hash.replace(/^#/, ""));
  const rawLower = link.toLowerCase();
  if (name.trim().startsWith("🇷🇺")) return null;
  if (link.includes("%F0%9F%87%B7%F0%9F%87%BA")) return null;
  if (rawLower.includes("#ru") || rawLower.includes("russia")) return null;

  const id = u.username;
  const address = u.hostname;
  const port = Number(u.port || "443");

  if (!id || !address || !port) return null;

  const type = (q.get("type") || "tcp").toLowerCase();
  const security = (q.get("security") || "none").toLowerCase();

  const tag = `auto-${String(index).padStart(3, "0")}`;

  const user = {
    id,
    encryption: q.get("encryption") || "none"
  };

  const flow = q.get("flow");
  if (flow) user.flow = flow;

  const outbound = {
    tag,
    protocol: "vless",
    settings: {
      vnext: [
        {
          address,
          port,
          users: [user]
        }
      ]
    },
    streamSettings: {
      network: type,
      security
    }
  };

  if (type === "tcp") {
    outbound.streamSettings.tcpSettings = {};
  }

  if (type === "grpc") {
    outbound.streamSettings.grpcSettings = {
      serviceName: q.get("serviceName") || "",
      authority: q.get("authority") || ""
    };

    if (q.get("mode") === "multi") {
      outbound.streamSettings.grpcSettings.multiMode = true;
    }
  }

  if (type === "xhttp") {
    outbound.streamSettings.xhttpSettings = {
      path: q.get("path") || "/",
      host: q.get("host") || "",
      mode: q.get("mode") || "auto"
    };
  }

  if (security === "tls") {
    outbound.streamSettings.tlsSettings = {
      serverName: q.get("sni") || q.get("host") || address,
      fingerprint: q.get("fp") || "chrome"
    };

    const alpn = q.get("alpn");
    if (alpn) {
      outbound.streamSettings.tlsSettings.alpn = alpn.split(",");
    }
  }

  if (security === "reality") {
    outbound.streamSettings.realitySettings = {
      show: false,
      serverName: q.get("sni") || address,
      fingerprint: q.get("fp") || "chrome",
      publicKey: q.get("pbk") || "",
      shortId: q.get("sid") || "",
      spiderX: q.get("spx") || "/"
    };
  }

  return outbound;
}

function safeDecode(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}
