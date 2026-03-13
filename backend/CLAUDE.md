# Backend Development Rules

Inherits all rules from the root CLAUDE.md. These are backend-specific additions.

## Running

```bash
source .venv/bin/activate
python run.py          # Start server on :8000
pytest tests/ -v       # Run tests
ruff check src/ tests/ # Lint
ruff format src/ tests/ # Format
```

## Code Patterns

### Service pattern

```python
class MyService:
    """One-line description of what this service owns."""

    async def do_thing(self, user_id: str, input: str) -> str:
        """Do the thing. Returns thing_id."""
        # 1. Validate input
        # 2. Fetch context from DB
        # 3. Process
        # 4. Store result
        # 5. Return ID
```

### Route pattern

```python
router = APIRouter()

@router.post("/v1/things", response_model=ThingResponse)
async def create_thing(
    req: ThingRequest,
    user_id: str = Depends(get_current_user),
):
    """What this endpoint does."""
    # Call service, return response model
```

### Model pattern

```python
class Thing(Base, TimestampMixin):
    __tablename__ = "things"

    thing_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # ... fields ...

    __table_args__ = (
        Index("ix_things_user_x", "user_id", "x"),
    )
```

## Important Constraints

- All IDs are string with type prefix: `evt_`, `plan_`, `exec_`, `mem_`, `apr_`, `brief_`
- Generate IDs using ULID for time-ordering: `from ulid import ULID; f"evt_{ULID()}"`
- All service methods are async
- Never return bare dicts from API endpoints — always Pydantic models
- Never import from OpenClaw or jarvis-tools — this is a standalone Python service
- Database access through SQLAlchemy async sessions only
- Config via pydantic-settings (env vars with JARVIS_ prefix)
