# A small VPC of its own: one public subnet, no NAT gateway (a NAT gateway is not in the free tier)
data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "pulse" {
  cidr_block           = "10.20.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "pulse" }
}

resource "aws_internet_gateway" "pulse" {
  vpc_id = aws_vpc.pulse.id

  tags = { Name = "pulse" }
}

resource "aws_subnet" "public" {
  vpc_id            = aws_vpc.pulse.id
  cidr_block        = "10.20.1.0/24"
  availability_zone = data.aws_availability_zones.available.names[0]

  tags = { Name = "pulse-public" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.pulse.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.pulse.id
  }

  tags = { Name = "pulse-public" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}
