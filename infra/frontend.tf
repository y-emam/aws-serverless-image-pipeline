locals {
  frontend_dir = "${path.module}/../frontend"
  mime_types = {
    html = "text/html; charset=utf-8"
    css  = "text/css; charset=utf-8"
    js   = "application/javascript; charset=utf-8"
    svg  = "image/svg+xml"
    ico  = "image/x-icon"
  }
}

resource "aws_s3_object" "frontend" {
  for_each = fileset(local.frontend_dir, "**/*.{html,css,js,svg,ico}")

  bucket       = aws_s3_bucket.this["frontend"].id
  key          = each.value
  source       = "${local.frontend_dir}/${each.value}"
  etag         = filemd5("${local.frontend_dir}/${each.value}")
  content_type = lookup(local.mime_types, reverse(split(".", each.value))[0], "application/octet-stream")
}
