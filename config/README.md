# Configuration Boundary

BUILD-001 deliberately does not implement the final configuration model.

Rules established now:

- runtime configuration comes from environment/runtime inputs;
- secrets are never committed;
- secrets are never logged;
- `.env` files are local-only and ignored by Git;
- `.env.example` may contain names/placeholders only, never credentials.

BUILD-002 owns typed configuration and secret definitions.
