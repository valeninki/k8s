# Valen's K0S Playground

This repository contains my HomeLab's k0s configurations and helm charts.

## Setup

### Architecture

* **Provider:** Hetzner Cloud
* **OS:** Fedora Linux 43 (Cloud Edition)
* **CPU:** Intel Xeon (Skylake, IBRS, no TSX) (4) @ 2.10 GHz

### Servers

| Hostname | Role          | CPU Cores | RAM | Machine Type |
|----------|---------------|-----------|-----|--------------|
| m1       | Control Plane | 4         | 8GB | CX33         |
| w1       | Worker        | 4         | 8GB | CX33         |
| w2       | Worker        | 4         | 8GB | CX33         |

---

## Roadmap

- [ ] Configure ssh for Forgejo with metallb load balancer and traefik.
- [ ] Switch to Kubernetes Gateway API.
- [ ] Add limits for application and services.
- [ ] Write custom grafana dashboard for monitoring cluster.

## Special Thanks

- [Taha](https://github.com/mt190502)   - Just like when I started with Nix, he explained why I should use Kubernetes again and said I could use his settings as a reference.
- [Kreato](https://github.com/kreatoo)  - Explained some Kubernetes troubleshooting things and gave advices for good.
- [Yağız](https://github.com/saveside)  - Helped for write Forgejo's helm chart.
