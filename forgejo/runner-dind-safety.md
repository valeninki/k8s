# Live Forgejo runner DinD safety patch

The `forgejo-runner` Deployment is currently created outside this Kustomize app. Reapply the tracked patch after any Deployment replacement:

```sh
kubectl -n forgejo-k8s patch deployment forgejo-runner --type=strategic --patch-file forgejo/runner-dind-safety.patch.json
kubectl -n forgejo-k8s rollout status deployment/forgejo-runner
```

The patch keeps the existing `docker-storage` volume name and DinD CPU/memory limits. It caps the `emptyDir` at 4Gi and starts a 30-minute Docker prune loop, deleting unused objects older than two hours. A restarted pod loses its Docker layer cache. `emptyDir.sizeLimit` is kubelet-enforced, not a hard filesystem quota on every node; watch node free space and pod ephemeral usage. This is not a per-job CPU, memory, or disk limit. Do not switch to native Kubernetes mode merely by changing runner config: confirm executor support and isolate pod-creation RBAC first.
