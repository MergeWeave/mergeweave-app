# UUID Migration Inventory - GitHub App

**Issue**: GitHub App uses `Integer` for `users.id`, but Public API uses `UUID`
**Impact**: Database schema mismatch prevents service startup
**Status**: Ready for migration

---

## 📋 Files Requiring Updates

### 🔴 CRITICAL - Model Definitions (7 files)

#### 1. **app/models/user.py** (PRIMARY KEY)
- **Line 38**: `id = Column(Integer, primary_key=True, index=True)`
- **Change to**: `id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)`
- **Add import**: `from sqlalchemy.dialects.postgresql import UUID` and `import uuid`

#### 2. **app/models/github.py** (COMPOSITE FILE - 2 changes)
- **Line 29**: `id = Column(Integer, primary_key=True)` (User model duplicate)
- **Line 49**: `user_id = Column(Integer, ForeignKey('users.id', ...))` ⚠️ FOREIGN KEY
- **Changes**:
  - User.id: Change to UUID
  - GitHubInstallation.user_id: Change to UUID

#### 3. **app/models/installation.py** (FOREIGN KEY)
- **Line 46**: `id = Column(Integer, primary_key=True, index=True)`
- **Line 49-54**: `user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), ...)`
- **Change**: user_id from Integer to UUID

#### 4. **app/models/repository.py**
- No user_id field - references installation_id only ✅ Safe

#### 5. **app/models/branch.py**
- No user_id field ✅ Safe

#### 6. **app/models/conflict.py**
- No direct user_id reference ✅ Safe

#### 7. **app/models/resolution.py**
- No direct user_id reference ✅ Safe

---

### 🟠 HIGH PRIORITY - Database Migrations (1 file)

#### 8. **app/database/migrations/versions/001_initial_schema.py**
- **Line 34**: `sa.Column('id', sa.Integer(), nullable=False)` in users table
- **Line 51**: `sa.Column('user_id', sa.Integer(), nullable=False)` in github_installations
- **Line 60**: `sa.ForeignKeyConstraint(['user_id'], ['users.id'], ...)`
- **Action**: Create NEW migration file (002_migrate_to_uuid.py) instead of modifying 001

---

### 🟡 MEDIUM PRIORITY - Type Hints & Function Signatures (6 files)

#### 9. **app/cache/keys.py**
- **Line 18**: `def user_installation(user_id: int) -> str:`
- **Change to**: `def user_installation(user_id: Union[int, UUID]) -> str:`
- **Reasoning**: Accept both during migration, then remove int support

#### 10. **app/cache/invalidation.py**
- **Line 31**: `user_id: Optional[int] = None`
- **Change to**: `user_id: Optional[UUID] = None`

#### 11. **app/auth/state.py** (3 changes)
- **Line 38**: `def generate_state(self, user_id: int, **extra_data) -> str:`
- **Line 73**: `def extract_user_id(self, state: str) -> int:`
- **Line 62**: JSON serialization of user_id (needs str() conversion)
- **Changes**:
  - Function signatures: int → UUID
  - Serialization: `str(user_id)` when storing in JSON
  - Deserialization: `UUID(state_data["user_id"])` when loading

#### 12. **app/api/oauth.py** (2 changes)
- **Line 30**: `user_id: int = Query(..., description="User ID from Public API")`
- **Line 82** (callback): Uses user_id extracted from state
- **Change**: Accept UUID in query parameter, update Pydantic validation

#### 13. **app/webhooks/handlers/installation.py**
- Uses `user.id` in logging (Lines ~30, ~60)
- **Change**: Convert to string for logging: `str(user.id)`

---

### 🟢 LOW PRIORITY - Test Fixtures (2 files)

#### 14. **tests/conftest.py**
- **Line 169-177**: `test_user` fixture creates User with auto-generated ID
- **Line 184**: `user_id=test_user.id` (passes to GitHubInstallation)
- **Change**: No changes needed - SQLAlchemy will auto-generate UUIDs
- **Verify**: Ensure `test_user.id` is UUID type in assertions

#### 15. **Test Files** (multiple)
- Most tests use fixture-generated IDs ✅ Will work automatically
- Hardcoded IDs like `installation_id=12345678` are for GitHub IDs, not user IDs ✅ Safe
- **No changes needed** if tests only use fixtures

---

## 🔧 Implementation Steps (Recommended Order)

### Step 1: Create New Migration (DO NOT modify 001)
```python
# app/database/migrations/versions/002_migrate_users_to_uuid.py

"""Migrate users.id from Integer to UUID

Revision ID: 002
Revises: 001
Create Date: 2025-11-18
"""

def upgrade() -> None:
    # 1. Add new UUID column
    op.add_column('users', sa.Column('id_new', UUID(as_uuid=True), nullable=True))

    # 2. Generate UUIDs for existing rows
    op.execute("UPDATE users SET id_new = gen_random_uuid()")

    # 3. Drop foreign key constraints
    op.drop_constraint('github_installations_user_id_fkey', 'github_installations')

    # 4. Add new user_id_new column
    op.add_column('github_installations',
                  sa.Column('user_id_new', UUID(as_uuid=True), nullable=True))

    # 5. Copy data (join old IDs to new UUIDs)
    op.execute("""
        UPDATE github_installations gi
        SET user_id_new = u.id_new
        FROM users u
        WHERE gi.user_id = u.id
    """)

    # 6. Drop old columns
    op.drop_column('github_installations', 'user_id')
    op.drop_column('users', 'id')

    # 7. Rename new columns
    op.alter_column('users', 'id_new', new_column_name='id')
    op.alter_column('github_installations', 'user_id_new', new_column_name='user_id')

    # 8. Set NOT NULL and re-add constraints
    op.alter_column('users', 'id', nullable=False)
    op.alter_column('github_installations', 'user_id', nullable=False)
    op.create_foreign_key(
        'github_installations_user_id_fkey',
        'github_installations', 'users',
        ['user_id'], ['id'],
        ondelete='CASCADE'
    )

    # 9. Re-create primary key
    op.create_primary_key('users_pkey', 'users', ['id'])
```

### Step 2: Update Model Files (in order)
1. ✅ app/models/user.py (primary key)
2. ✅ app/models/github.py (User model + user_id FK)
3. ✅ app/models/installation.py (user_id FK)

### Step 3: Update Type Hints
4. ✅ app/cache/keys.py
5. ✅ app/cache/invalidation.py
6. ✅ app/auth/state.py (includes JSON serialization)
7. ✅ app/api/oauth.py

### Step 4: Update Webhook Handlers
8. ✅ app/webhooks/handlers/installation.py (logging)

### Step 5: Verify Tests
9. ✅ Run unit tests: `pytest tests/unit -v`
10. ✅ Run integration tests: `pytest tests/integration -v`

---

## 📊 Impact Summary

| Category | Files | Lines Changed | Risk Level |
|----------|-------|---------------|------------|
| Models | 3 | ~10 | 🔴 Critical |
| Migrations | 1 (new) | ~50 | 🔴 Critical |
| Type Hints | 4 | ~8 | 🟡 Medium |
| API Endpoints | 1 | ~2 | 🟡 Medium |
| Handlers | 1 | ~2 | 🟢 Low |
| Tests | 0 | 0 | 🟢 Low (auto-fix) |
| **TOTAL** | **10** | **~72** | **🟠 High** |

---

## ⚠️ Potential Breaking Changes

### API Changes
- **OAuth endpoints** will now accept UUID strings instead of integers
- **Frontend/Public API** must send UUIDs in `user_id` query parameters

### Serialization Changes
- **State parameters**: user_id will be serialized as UUID string
- **Cache keys**: Will use UUID string representation (no breaking change)
- **Logging**: user_id will appear as UUID string in logs

### Database Compatibility
- **⚠️ REQUIRES DATA MIGRATION**: Cannot simply change column type
- **Downtime**: Minimal (<30 seconds) during migration
- **Rollback**: Complex - requires reverse migration

---

## ✅ Validation Checklist

After implementation:
- [ ] Service starts without database schema errors
- [ ] User fixtures in tests generate valid UUIDs
- [ ] OAuth flow accepts UUID user_id from Public API
- [ ] State parameters serialize/deserialize UUIDs correctly
- [ ] Cache keys work with UUID user_ids
- [ ] Foreign key constraints are valid
- [ ] All 319+ unit tests still pass
- [ ] Integration tests with database work
- [ ] No hardcoded integer user IDs remain in code

---

## 🚀 Ready to Proceed?

**Estimated Time**: 1-2 hours
**Risk Level**: Medium-High (requires careful migration)
**Rollback Strategy**: Keep migration 001 intact, can roll back to it

**Next Command**:
```bash
# Start with model updates, then create migration
python -m pytest tests/unit -v --tb=short
```
