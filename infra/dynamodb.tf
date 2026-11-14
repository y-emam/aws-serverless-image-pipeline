resource "aws_dynamodb_table" "images" {
  name         = "${local.name}-images"
  billing_mode = "PAY_PER_REQUEST" # on-demand: no idle cost, no capacity planning
  hash_key     = "image_id"

  attribute {
    name = "image_id"
    type = "S"
  }

  server_side_encryption {
    enabled = false # false = AWS owned key (free, still encrypted at rest)
  }
}
