target "docker-metadata-action" {}

// Ours, never a default: `our_version` in .mise/lib.sh derives it from the submodule.
variable "VERSION" {
  default = ""
}

// The pinned upstream commit; upstream's community build passes the same.
variable "BUILD_ID" {
  default = ""
}

variable "SOURCE" {
  default = "https://github.com/astrateam-net/learnhouse"
}

group "default" {
  targets = ["image-local"]
}

// Upstream's own Dockerfile on the assembled tree, with the args its community build uses:
// `mise run build:context` first, so the series and overlay are in lib/learnhouse. CI sets the
// other OCI labels.
target "image" {
  inherits = ["docker-metadata-action"]
  context  = "lib/learnhouse"
  args = {
    LEARNHOUSE_PUBLIC = "true"
    BUILD_ID          = "${BUILD_ID}"
  }
  labels = {
    "org.opencontainers.image.source" = "${SOURCE}"
  }
}

target "image-local" {
  inherits = ["image"]
  output   = ["type=docker"]
}

// Our Swarm nodes are amd64.
target "image-all" {
  inherits  = ["image"]
  platforms = ["linux/amd64"]
}
