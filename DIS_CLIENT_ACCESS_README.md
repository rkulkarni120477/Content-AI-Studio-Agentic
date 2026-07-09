# Source Library Client Access

This build does not require setting client access in PowerShell every time.
CAS reads Source Library access from:

`config/dis_access.json`

Default included config:

```json
{
  "default_client_id": "cengage",
  "default_tenant_id": "cengage",
  "available_clients": ["aim", "cengage"],
  "super_admin_usernames": ["admin"],
  "client_admin_map": {
    "cengage_admin": "cengage",
    "aim_admin": "aim"
  },
  "user_client_map": {
    "cengage_user": "cengage",
    "aim_user": "aim"
  }
}
```

## How it works

- Existing CAS login remains unchanged.
- Username/password are still created in the normal CAS users table.
- DIS/Source Library access is mapped by username.
- `admin / admin123` is configured as Source Library super admin by default.
- Super admin sees the AIM/Cengage client dropdown in Source Library.
- Client admins/users are automatically locked to their mapped client.

## Create access

1. Create the user in CAS Admin/User Management as usual.
2. Add username to `config/dis_access.json`:

Client admin:

```json
"client_admin_map": {
  "new_cengage_admin": "cengage"
}
```

Client user/viewer:

```json
"user_client_map": {
  "new_cengage_user": "cengage"
}
```

Super admin:

```json
"super_admin_usernames": ["admin", "new_super_admin"]
```

3. Restart CAS backend.

## Optional API to update access config

Super admin can call:

- `GET /api/v1/source-library/admin/access-config`
- `PUT /api/v1/source-library/admin/access-config`

The PUT payload is the same JSON shape shown above.

## Production recommendation

For production, move this mapping into real database tables:

- clients
- user_client_access

The current JSON config is intended for immediate integration and local/UAT use.
