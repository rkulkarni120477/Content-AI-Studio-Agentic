# Agent Builder - Actual System Test Credentials

**Date**: October 7, 2026  
**System**: Content AI Studio - Agent Builder  
**Database**: SQLite (content_ai.db)

---

## 🔑 Actual Users in System

### 1. Platform Administrator
```
Username: admin
Email: admin@localhost
Role: Platform Admin
Status: Active ✅
Password: [Needs to be reset/set]
Project: None (Platform-wide access)
```

**Capabilities**: Full system access, manage all projects, users, and settings

---

### 2. Test Organization Users

**Organization**: Test Organization  
**Organization Slug**: test-org  
**Organization ID**: 1

```
Username: testuser
Email: test@localhost
Role: Author
Status: Active ✅
Password: [Needs to be reset/set]
Project: Test Organization (ID: 1)
```

**Capabilities**: Create agents, run tests, view results

---

### 3. Academian Education Pvt Ltd (Production Organization)

**Organization**: Academian Education Pvt Ltd  
**Organization Code/Slug**: academian-007  
**Organization ID**: 2  
**Max Users**: 50  
**Current Users**: 3

#### 3.1 Tenant Admin
```
Username: Rahul
Email: (Not set)
Role: Admin (Tenant-level)
Status: Active ✅
Password: [Needs to be reset/set]
Project: Academian Education Pvt Ltd (ID: 2)
```

**Capabilities**: Manage project, create agents, manage team members

---

#### 3.2 Author
```
Username: Ranjith_User
Email: (Not set)
Role: Author
Status: Active ✅
Password: [Needs to be reset/set]
Project: Academian Education Pvt Ltd (ID: 2)
```

**Capabilities**: Create and run agents, view project results

---

#### 3.3 Reviewer
```
Username: Ranjith_PromptMgr
Email: (Not set)
Role: Reviewer
Status: Active ✅
Password: [Needs to be reset/set]
Project: Academian Education Pvt Ltd (ID: 2)
```

**Capabilities**: Review agent outputs, approve changes, manage versions

---

## 🔧 How to Set Passwords for Users

Since passwords are hashed in the database, use one of these methods:

### Method 1: Create Admin User Script
```bash
python scripts/create_admin.py --username rahul --password YourPassword123 --platform-admin
```

### Method 2: Direct Database Reset (for testing only)
```python
from app.core.security import hash_password
import sqlite3

password = "TestPassword123"
hashed = hash_password(password)

conn = sqlite3.connect('content_ai.db')
cursor = conn.cursor()
cursor.execute('UPDATE users SET password_hash = ? WHERE username = ?', 
               (hashed, 'Rahul'))
conn.commit()
```

### Method 3: Using API (if implemented)
```bash
curl -X POST http://localhost:8000/api/v1/users/reset-password \
  -H "Content-Type: application/json" \
  -d '{"username": "Rahul", "new_password": "NewPassword123"}'
```

---

## 🧪 Recommended Test Credentials

For testing purposes, set these passwords:

```
Username: Rahul
Password: Rahul@123

Username: Ranjith_User
Password: Ranjith@123

Username: Ranjith_PromptMgr
Password: RanjithReviewer@123

Username: testuser
Password: TestUser@123

Username: admin
Password: AdminPass@123
```

---

## 📋 Test Workflows with Actual Users

### Workflow 1: Organization Admin Access
```
Login: Rahul / Rahul@123
Organization: Academian Education Pvt Ltd
Expected: 
  - Can view all team members
  - Can create new agents
  - Can manage project settings
  - Can view all project costs
```

### Workflow 2: Author Testing
```
Login: Ranjith_User / Ranjith@123
Organization: Academian Education Pvt Ltd
Expected:
  - Can create new agents
  - Can run agents
  - Can view own run history
  - Cannot manage project settings
```

### Workflow 3: Reviewer Workflow
```
Login: Ranjith_PromptMgr / RanjithReviewer@123
Organization: Academian Education Pvt Ltd
Expected:
  - Can review agent outputs
  - Can approve/reject changes
  - Can view all project runs
  - Cannot create new agents
```

### Workflow 4: Platform Admin
```
Login: admin / AdminPass@123
Organization: All organizations
Expected:
  - Can manage all users across all organizations
  - Can view system-wide analytics
  - Can manage agent templates
  - Can configure system settings
```

---

## 🗂️ Organization Details

### Test Organization
```
Name: Test Organization
Slug: test-org
ID: 1
Active: Yes
Created: 2026-10-06
Users: 1 (testuser)
```

### Academian Education Pvt Ltd
```
Name: Academian Education Pvt Ltd
Slug: academian-007
Organization Code: academian-007
ID: 2
Client Name: Academian Education Pvt Ltd
Active: Yes
Max Users: 50
Current Users: 3
Created: 2026-10-07
Azure Auth: Enabled
Users:
  - Rahul (Admin)
  - Ranjith_User (Author)
  - Ranjith_PromptMgr (Reviewer)
```

---

## ✅ Quick Reference

### Platform Admin
```
Email: admin@localhost
Username: admin
Password: AdminPass@123 (recommended)
```

### Tenant Admin (Academian)
```
Username: Rahul
Password: Rahul@123 (recommended)
Organization: Academian Education Pvt Ltd (academian-007)
```

### Author (Academian)
```
Username: Ranjith_User
Password: Ranjith@123 (recommended)
Organization: Academian Education Pvt Ltd (academian-007)
```

### Reviewer (Academian)
```
Username: Ranjith_PromptMgr
Password: RanjithReviewer@123 (recommended)
Organization: Academian Education Pvt Ltd (academian-007)
```

---

## 🔗 Application Links

- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs
- **Organization Code**: academian-007 (when prompted)

---

## 📝 Notes

1. **Passwords**: The actual passwords in the database are hashed with SHA-256
2. **Organization Selection**: After login, you may be prompted to select an organization
3. **Azure Auth**: Academian Education organization has Azure authentication enabled
4. **User Management**: To add more users, use the "Add User" button in the organization settings
5. **Role Assignment**: User roles can be changed from the Members Directory page

---

## 🚀 Next Steps

1. **Set Passwords**: Use the methods above to set passwords for testing
2. **Test Login**: Use each credential set to verify role-based access
3. **Test Workflows**: Follow the workflows for each role
4. **Document Issues**: Note any permission or access issues found

---

**End of Document**
