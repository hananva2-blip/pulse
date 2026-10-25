# Canonical publishes the current Ubuntu 24.04 AMI id here; Ubuntu ships with the SSM agent
data "aws_ssm_parameter" "ubuntu" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

resource "aws_instance" "node" {
  ami                         = data.aws_ssm_parameter.ubuntu.value
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.public.id
  vpc_security_group_ids      = [aws_security_group.node.id]
  iam_instance_profile        = aws_iam_instance_profile.node.name
  associate_public_ip_address = true

  user_data                   = templatefile("${path.module}/cloud-init.sh.tftpl", { k3s_version = var.k3s_version })
  user_data_replace_on_change = true

  # "unlimited" (the t3 default) bills CPU bursts above the baseline; "standard" never costs extra
  credit_specification {
    cpu_credits = "standard"
  }

  # IMDSv2 only, and one hop: pods on the node cannot reach the instance's credentials
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_gb
    encrypted             = true
    delete_on_termination = true
  }

  tags = { Name = "pulse-k3s" }

  lifecycle {
    # A new Ubuntu AMI must not replace the running node on the next plan (a SkyWatch lesson)
    ignore_changes = [ami]
  }
}
