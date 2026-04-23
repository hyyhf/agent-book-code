---
name: REST API Design
description: RESTful API design conventions and patterns
tags: [api, rest, http]
---
# REST API Design

## URL Patterns
- `GET /items` - list all items
- `GET /items/:id` - get single item
- `POST /items` - create new item
- `PUT /items/:id` - update entire item
- `PATCH /items/:id` - partial update
- `DELETE /items/:id` - delete item

## HTTP Status Codes

### Success (2xx)
- `200 OK` - Standard success response
- `201 Created` - Resource created successfully
- `204 No Content` - Success but no content to return

### Client Error (4xx)
- `400 Bad Request` - Invalid request
- `401 Unauthorized` - Authentication required
- `403 Forbidden` - Authenticated but not authorized
- `404 Not Found` - Resource doesn't exist
- `409 Conflict` - Resource conflict

### Server Error (5xx)
- `500 Internal Server Error` - Generic server error
- `502 Bad Gateway` - Invalid response from upstream
- `503 Service Unavailable` - Server temporarily unavailable

## Request/Response Examples

### Create Resource
```http
POST /users
Content-Type: application/json

{
  "name": "John Doe",
  "email": "john@example.com"
}

Response: 201 Created
Location: /users/123
{
  "id": 123,
  "name": "John Doe",
  "email": "john@example.com",
  "created_at": "2024-01-01T00:00:00Z"
}
```

### Error Response
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid input data",
    "details": [
      {
        "field": "email",
        "message": "Must be a valid email address"
      }
    ]
  }
}
```

## Best Practices

### Versioning
- URL versioning: `/api/v1/users`
- Header versioning: `Accept: application/vnd.api.v1+json`

### Pagination
```json
{
  "data": [...],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total": 100,
    "total_pages": 5
  },
  "links": {
    "self": "/items?page=1",
    "next": "/items?page=2",
    "prev": null,
    "last": "/items?page=5"
  }
}
```

### Filtering, Sorting, Searching
- Filtering: `/items?category=books&price_lt=50`
- Sorting: `/items?sort=-created_at,price`
- Searching: `/items?q=keyword`

### Rate Limiting
- Headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`
- Response: `429 Too Many Requests`