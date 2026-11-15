# Sample Lambda test events

Test events for the five functions. Paste one into the Lambda console's **Test** tab (Event JSON), or use it as a reference for the shape API Gateway sends with proxy integration. Only the fields the functions read are included.

Notes:

- `body` is a JSON **string**, so the quotes inside it are escaped. API Gateway builds this for real requests.
- `requestContext.authorizer.claims.sub` is a placeholder. A real call carries the Cognito user's opaque ID, and the functions log it in a `caller` field. Without it, they log `unknown`.
- All IDs and names are synthetic.

| File | Function | Expected result |
| --- | --- | --- |
| `get-provider-found.json` | provider-get | 200 (needs `p-1002` in the table) |
| `get-provider-with-caller.json` | provider-get | 200, log shows `caller: test-user-123` |
| `get-provider-null-authorizer.json` | provider-get | 200, log shows `caller: unknown` |
| `get-provider-not-found.json` | provider-get | 404 |
| `get-provider-missing-id.json` | provider-get | 400 |
| `create-provider-valid.json` | provider-create | 201 on the first run, 409 on the second; stored with lowercase `city` and `specialty` and uppercase `status` |
| `create-provider-missing-city.json` | provider-create | 400 |
| `create-provider-invalid-status.json` | provider-create | 400 |
| `create-provider-not-json.json` | provider-create | 400 |
| `update-provider-valid.json` | provider-update | 200 (needs `p-2001`, so run the create event first) |
| `update-provider-id-mismatch.json` | provider-update | 400 |
| `update-provider-missing-field.json` | provider-update | 400 |
| `delete-provider.json` | provider-delete | 204 on the first run, 404 on the second |
| `delete-provider-invalid-id.json` | provider-delete | 400 |
| `list-providers-no-filter.json` | provider-list | 200, all providers |
| `list-providers-null-params.json` | provider-list | 200, all providers |
| `list-providers-city-filter.json` | provider-list | 200, city filter is case-insensitive |
| `list-providers-city-and-specialty.json` | provider-list | 200, providers matching both |
| `list-providers-status-lowercase.json` | provider-list | 200, inactive providers |
| `list-providers-invalid-status.json` | provider-list | 400 |

Suggested order for a full pass: create, update, get, list, delete.
