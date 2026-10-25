# Only the website is open: HTTP and HTTPS to Traefik. No SSH and no Kubernetes API from outside;
# admin access goes through AWS Systems Manager (see iam.tf and the README).
resource "aws_security_group" "node" {
  name        = "pulse-node"
  description = "Pulse K3s node: web traffic in, anything out"
  vpc_id      = aws_vpc.pulse.id

  tags = { Name = "pulse-node" }
}

# trivy:ignore:AVD-AWS-0107 The status page and dashboard are a public website
resource "aws_vpc_security_group_ingress_rule" "web" {
  for_each = { http = 80, https = 443 }

  security_group_id = aws_security_group.node.id
  description       = "Public ${upper(each.key)} to the Traefik ingress"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = each.value
  to_port           = each.value
}

# trivy:ignore:AVD-AWS-0104 Monitoring checks any site on the internet, and the node pulls images from GHCR
resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.node.id
  description       = "Checks of monitored sites, image pulls, SSM and Telegram"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}
