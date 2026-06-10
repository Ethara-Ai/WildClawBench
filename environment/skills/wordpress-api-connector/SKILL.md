---
name: wordpress-api-connector
description: >
  WordPress REST API (Mock) mock HTTP API. Base URL is provided via the
  `WORDPRESS_API_URL` environment variable. 12 endpoint(s) across GET, POST, PUT, DELETE
  covering posts, pages, categories, tags, comments, media, and users.
metadata: {"clawdbot":{"emoji":"🔌"}}
---

# WordPress REST API (Mock)

Mock HTTP API implementing a subset of the WordPress REST API. **All requests go to the
base URL in `$WORDPRESS_API_URL`.** All paths below are relative to that base and live
under the `/wp-json/wp/v2` namespace. Auth headers are mocked (any token is accepted).
Responses are deterministic fixtures.

## Base URL

| Variable | Purpose |
|----------|---------|
| `WORDPRESS_API_URL` | Base URL for all requests (e.g. `http://wordpress-api:8065`) |

---

## Health

```
GET /health
```

Returns `{"status": "ok"}`.

---

## Posts

### List posts

Returns posts matching the given filters.

```
GET /wp-json/wp/v2/posts
```

| Parameter | Type | In | Required | Description |
|-----------|------|------|----------|-------------|
| `status` | string | query | no | Filter by status (e.g. `publish`, `draft`) |
| `author` | integer | query | no | Filter by author user ID |
| `search` | string | query | no | Free-text search across post content |
| `categories` | integer | query | no | Filter by category ID |
| `per_page` | integer | query | no | Results per page, 1–100 (default 10) |

Response: array of post objects with fields `id`, `title`, `slug`, `status`, `author`, `content`, `excerpt`, `category_ids`, `tag_ids`, `comment_status`, `date`, `modified`.

### Create post

Creates a new post. Returns the created post with status `201`.

```
POST /wp-json/wp/v2/posts
```

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | yes | Post title |
| `content` | string | no | Post body (default `""`) |
| `status` | string | no | Post status (default `draft`) |
| `author` | integer | no | Author user ID (default `1`) |
| `excerpt` | string | no | Short excerpt |
| `categories` | array[int] | no | Category IDs |
| `tags` | array[int] | no | Tag IDs |

### Get post

Returns a single post by ID. Returns `404` with `{"error": …}` if not found.

```
GET /wp-json/wp/v2/posts/{post_id}
```

| Parameter | Type | In | Required | Description |
|-----------|------|------|----------|-------------|
| `post_id` | integer | path | yes | Post ID |

### Update post

Updates an existing post. Only supplied fields are changed. Returns `404` if not found.

```
PUT /wp-json/wp/v2/posts/{post_id}
```

| Parameter | Type | In | Required | Description |
|-----------|------|------|----------|-------------|
| `post_id` | integer | path | yes | Post ID |

**Request body** (all optional)

| Field | Type | Description |
|-------|------|-------------|
| `title` | string | Updated title |
| `content` | string | Updated body |
| `status` | string | Updated status |
| `excerpt` | string | Updated excerpt |
| `categories` | array[int] | Updated category IDs |
| `tags` | array[int] | Updated tag IDs |

### Delete post

Deletes a post by ID. Returns `404` if not found.

```
DELETE /wp-json/wp/v2/posts/{post_id}
```

| Parameter | Type | In | Required | Description |
|-----------|------|------|----------|-------------|
| `post_id` | integer | path | yes | Post ID |

---

## Pages

### List pages

Returns pages matching the given filters.

```
GET /wp-json/wp/v2/pages
```

| Parameter | Type | In | Required | Description |
|-----------|------|------|----------|-------------|
| `status` | string | query | no | Filter by status (default `publish`) |
| `per_page` | integer | query | no | Results per page, 1–100 (default 10) |

Response: array of page objects with fields `id`, `title`, `slug`, `status`, `author`, `content`, `date`, `modified`, `parent`.

---

## Taxonomies

### List categories

Returns all categories.

```
GET /wp-json/wp/v2/categories
```

This endpoint takes no parameters. Response: array of category objects with fields `id`, `name`, `slug`, `description`, `parent`, `count`.

### List tags

Returns all tags.

```
GET /wp-json/wp/v2/tags
```

This endpoint takes no parameters. Response: array of tag objects with fields `id`, `name`, `slug`, `description`, `count`.

---

## Comments

### List comments

Returns comments matching the given filters.

```
GET /wp-json/wp/v2/comments
```

| Parameter | Type | In | Required | Description |
|-----------|------|------|----------|-------------|
| `post` | integer | query | no | Filter by post ID |
| `status` | string | query | no | Filter by status (default `approved`) |

Response: array of comment objects with fields `id`, `post`, `author_name`, `author_email`, `content`, `status`, `date`, `parent`.

### Create comment

Creates a new comment. Returns the created comment with status `201`; returns `404` if the referenced post does not exist.

```
POST /wp-json/wp/v2/comments
```

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `post` | integer | yes | Post ID being commented on |
| `author_name` | string | yes | Commenter name |
| `author_email` | string | yes | Commenter email |
| `content` | string | yes | Comment body |
| `parent` | integer | no | Parent comment ID for threading (default `0`) |

---

## Media & Users

### List media

Returns all media items.

```
GET /wp-json/wp/v2/media
```

This endpoint takes no parameters. Response: array of media objects with fields `id`, `title`, `slug`, `media_type`, `mime_type`, `source_url`, `alt_text`, `author`, `post`, `date`.

### List users

Returns all users.

```
GET /wp-json/wp/v2/users
```

This endpoint takes no parameters. Response: array of user objects with fields `id`, `name`, `slug`, `description`, `url`, `roles`, `avatar_url`.

---

## Conventions

- Single-resource reads return the object directly; list endpoints return a JSON array.
- Not-found reads/updates/deletes return HTTP `404` with body `{"error": …}`.
- Create and update accept JSON bodies; create endpoints respond with HTTP `201`.

The audit log of every call the agent makes is available at
`$WORDPRESS_API_URL/audit/requests` (used for grading).
