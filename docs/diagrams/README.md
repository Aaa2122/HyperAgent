# Architecture diagrams

The versioned `.puml` files provide maintainable technical views of the architecture. The project README uses simplified editorial infographics stored in `docs/assets/`.

Render every PNG from the repository root with:

```powershell
.\docs\diagrams\render.ps1
```

The script sends the PlantUML sources to Kroki and writes the technical renders to `docs/diagrams/rendered/`.
