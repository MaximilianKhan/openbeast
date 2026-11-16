# Permissions

Roles live in `ferrule/auth/roles.py`. A rule is an action pattern such as
`jobs.cancel`, `jobs.*` or `*`; a `:own` suffix limits it to objects the
caller owns.

Roles inherit from the roles in `inherits`. A deny anywhere in a role's
inheritance chain wins over any allow.

## Changing roles per deployment

```
[auth.roles.support]
allow+ = audit.read      ; appends to the built-in allow list
```

A new role needs `inherits` and `allow`:

```
[auth.roles.oncall]
inherits = operator
allow = jobs.cancel
```
