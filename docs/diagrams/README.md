# Architecture diagrams

The versioned `.puml` files are the source of truth for the architecture visuals used by the project README.

Render every PNG from the repository root with:

```powershell
.\docs\diagrams\render.ps1
```

The script sends the PlantUML sources to Kroki and replaces the matching files in `docs/assets/`.
