# IAM policies

Each Lambda function has its own execution role. A role has three parts:

1. **Trust policy** (`lambda-trust-policy.json`): only the Lambda service may assume the role.
2. **Managed policy `AWSLambdaBasicExecutionRole`**: lets the function write its logs to CloudWatch Logs.
3. **Inline permissions policy** (one file per function below): exactly one DynamoDB action on exactly one table.

| Function | Role | Inline policy | DynamoDB action |
| --- | --- | --- | --- |
| provider-get | provider-get-role | `provider-get-policy.json` | `GetItem` |
| provider-create | provider-create-role | `provider-create-policy.json` | `PutItem` |
| provider-update | provider-update-role | `provider-update-policy.json` | `UpdateItem` |
| provider-delete | provider-delete-role | `provider-delete-policy.json` | `DeleteItem` |
| provider-list | provider-list-role | `provider-list-policy.json` | `Scan` |

Replace `<region>` and `<account-id>` with your own values when applying a policy. A function that has a bug or is compromised can then only perform its own single action on the `providers` table: the create role cannot delete, and the get role cannot write.
