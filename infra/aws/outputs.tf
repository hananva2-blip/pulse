output "public_ip" {
  description = "Public address of the node"
  value       = aws_instance.node.public_ip
}

output "url" {
  description = "Pulse, once the chart is installed"
  value       = "http://${aws_instance.node.public_ip}"
}

output "shell" {
  description = "A shell on the node, without SSH"
  value       = "aws ssm start-session --profile pulse --region ${var.region} --target ${aws_instance.node.id}"
}

output "kubernetes_api_tunnel" {
  description = "Forward the K3s API to localhost:6443 through SSM, then use the node's kubeconfig"
  value       = "aws ssm start-session --profile pulse --region ${var.region} --target ${aws_instance.node.id} --document-name AWS-StartPortForwardingSession --parameters portNumber=6443,localPortNumber=6443"
}
