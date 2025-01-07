import React, { useCallback, useState } from 'react';
import ReactFlow, { Controls, useNodesState, useEdgesState, addEdge } from 'reactflow';
import 'reactflow/dist/style.css';

const Canvas = () => {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedNode, setSelectedNode] = useState(null);
  const [terraformCode, setTerraformCode] = useState('');

  const onDrop = useCallback(
    (event) => {
      event.preventDefault();
      const type = event.dataTransfer.getData('application/reactflow');
      const position = { x: event.clientX, y: event.clientY };

      const newNode = {
        id: `${type}-${Date.now()}`,
        type: 'default',
        position,
        data: { label: `${type}`, config: {} }, // Add a config object
      };

      setNodes((nds) => nds.concat(newNode));
    },
    [setNodes]
  );

  const onNodeClick = useCallback(
    (event, node) => {
      setSelectedNode(node);
    },
    []
  );

  const onConnect = useCallback(
    (params) => {
      const sourceNode = nodes.find((n) => n.id === params.source);
      const targetNode = nodes.find((n) => n.id === params.target);

      // Allow only EC2 → SG connections
      if ((sourceNode.data.label === 'ec2' && targetNode.data.label === 'sg') || (sourceNode.data.label === 'sg' && targetNode.data.label === 'ec2')) {
        // Add the Security Group ID to the EC2 instance's configuration
        const updatedSourceNode = {
          ...sourceNode,
          data: {
            ...sourceNode.data,
            config: {
              ...sourceNode.data.config,
              securityGroupIds: sourceNode.data.config.securityGroupIds
                ? `${sourceNode.data.config.securityGroupIds},${targetNode.id}`
                : targetNode.id,
            },
          },
        };

        // Update the nodes state
        setNodes((nds) =>
          nds.map((n) => (n.id === updatedSourceNode.id ? updatedSourceNode : n))
        );

        // Add the edge to the edges state
        setEdges((eds) => addEdge(params, eds));
      } else {
        console.log('Invalid connection:', params); // Debugging line
      }
    },
    [nodes]
  );

  const handleConfigChange = (key, value) => {
    const updatedNode = {
      ...selectedNode,
      data: {
        ...selectedNode.data,
        config: {
          ...selectedNode.data.config,
          [key]: value,
        },
      },
    };
    setNodes((nds) => nds.map((n) => (n.id === updatedNode.id ? updatedNode : n)));
    setSelectedNode(updatedNode);
  };

  const handleDeleteNode = () => {
    setNodes((nds) => nds.filter((n) => n.id !== selectedNode.id));
    setEdges((eds) => eds.filter((e) => e.source !== selectedNode.id && e.target !== selectedNode.id));
    setSelectedNode(null);
  };

  const generateTerraformCode = (nodes, edges) => {
    let terraformCode = '';

    // Generate Terraform code for each node
    nodes.forEach((node) => {
      switch (node.data.label) {
        case 'ec2':
          terraformCode += `
resource "aws_instance" "${node.id}" {
  ami           = "${node.data.config.ami || 'ami-0c55b159cbfafe1f0'}"
  instance_type = "${node.data.config.instanceType || 't3.medium'}"

  subnet_id     = "${node.data.config.subnetId || ''}"
  security_groups = [${node.data.config.securityGroupIds
            ? node.data.config.securityGroupIds
                .split(',')
                .map((sgId) => `aws_security_group.${sgId}.id`)
                .join(', ')
            : ''
          }]

  tags = {
    ${node.data.config.tags ? node.data.config.tags : 'Name = "example"'}
  }
}\n\n`;
          break;

        case 's3':
          terraformCode += `
resource "aws_s3_bucket" "${node.id}" {
  bucket = "${node.data.config.bucketName || 'my-bucket'}"
  acl    = "private"

  versioning {
    enabled = ${node.data.config.versioning === 'enabled'}
  }

  tags = {
    ${node.data.config.tags ? node.data.config.tags : 'Name = "example"'}
  }
}\n\n`;
          break;

        case 'vpc':
          terraformCode += `
resource "aws_vpc" "${node.id}" {
  cidr_block = "${node.data.config.cidrBlock || '10.0.0.0/16'}"

  enable_dns_support   = ${node.data.config.enableDnsSupport === 'true'}
  enable_dns_hostnames = ${node.data.config.enableDnsHostnames === 'true'}

  tags = {
    ${node.data.config.tags ? node.data.config.tags : 'Name = "example"'}
  }
}\n\n`;
          break;

        case 'sg':
          terraformCode += `
resource "aws_security_group" "${node.id}" {
  name        = "${node.data.config.sgName || 'my-security-group'}"
  description = "${node.data.config.description || 'Managed by InfraQ'}"

  vpc_id      = "${node.data.config.vpcId || ''}"

  tags = {
    ${node.data.config.tags ? node.data.config.tags : 'Name = "example"'}
  }
}\n\n`;
          break;

        default:
          break;
      }
    });

    return terraformCode;
  };

  const handleGenerateTerraformCode = () => {
    const code = generateTerraformCode(nodes, edges);
    setTerraformCode(code);
  };

  const exportTerraformCode = () => {
    const code = generateTerraformCode(nodes, edges);
    const blob = new Blob([code], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'infraq.tf';
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div style={{ height: '100vh', width: '100%' }} onDrop={onDrop} onDragOver={(event) => event.preventDefault()}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={onNodeClick}
        onConnect={onConnect}
      >
        <Controls />
      </ReactFlow>
      {selectedNode && (
        <div style={{ position: 'absolute', top: '10px', right: '10px', background: 'white', padding: '10px', border: '1px solid #ddd', width: '300px' }}>
          <h3>Configure {selectedNode.data.label}</h3>
          {selectedNode.data.label === 'ec2' && (
            <div>
              <div>
                <label>Instance Type:</label>
                <input
                  type="text"
                  value={selectedNode.data.config.instanceType || ''}
                  onChange={(e) => handleConfigChange('instanceType', e.target.value)}
                />
              </div>
              <div>
                <label>AMI ID:</label>
                <input
                  type="text"
                  value={selectedNode.data.config.ami || ''}
                  onChange={(e) => handleConfigChange('ami', e.target.value)}
                />
              </div>
              <div>
                <label>Subnet ID:</label>
                <input
                  type="text"
                  value={selectedNode.data.config.subnetId || ''}
                  onChange={(e) => handleConfigChange('subnetId', e.target.value)}
                />
              </div>
              <div>
                <label>Security Group IDs:</label>
                <input
                  type="text"
                  value={selectedNode.data.config.securityGroupIds || ''}
                  onChange={(e) => handleConfigChange('securityGroupIds', e.target.value)}
                  readOnly // Make the field read-only since it's auto-populated
                />
              </div>
              <div>
                <label>Tags:</label>
                <input
                  type="text"
                  value={selectedNode.data.config.tags || ''}
                  onChange={(e) => handleConfigChange('tags', e.target.value)}
                />
              </div>
            </div>
          )}
          {selectedNode.data.label === 's3' && (
            <div>
              <div>
                <label>Bucket Name:</label>
                <input
                  type="text"
                  value={selectedNode.data.config.bucketName || ''}
                  onChange={(e) => handleConfigChange('bucketName', e.target.value)}
                />
              </div>
              <div>
                <label>Versioning:</label>
                <select
                  value={selectedNode.data.config.versioning || 'disabled'}
                  onChange={(e) => handleConfigChange('versioning', e.target.value)}
                >
                  <option value="enabled">Enabled</option>
                  <option value="disabled">Disabled</option>
                </select>
              </div>
              <div>
                <label>Tags:</label>
                <input
                  type="text"
                  value={selectedNode.data.config.tags || ''}
                  onChange={(e) => handleConfigChange('tags', e.target.value)}
                />
              </div>
            </div>
          )}
          {selectedNode.data.label === 'vpc' && (
            <div>
              <div>
                <label>CIDR Block:</label>
                <input
                  type="text"
                  value={selectedNode.data.config.cidrBlock || ''}
                  onChange={(e) => handleConfigChange('cidrBlock', e.target.value)}
                />
              </div>
              <div>
                <label>Enable DNS Support:</label>
                <select
                  value={selectedNode.data.config.enableDnsSupport || 'true'}
                  onChange={(e) => handleConfigChange('enableDnsSupport', e.target.value)}
                >
                  <option value="true">True</option>
                  <option value="false">False</option>
                </select>
              </div>
              <div>
                <label>Enable DNS Hostnames:</label>
                <select
                  value={selectedNode.data.config.enableDnsHostnames || 'true'}
                  onChange={(e) => handleConfigChange('enableDnsHostnames', e.target.value)}
                >
                  <option value="true">True</option>
                  <option value="false">False</option>
                </select>
              </div>
              <div>
                <label>Tags:</label>
                <input
                  type="text"
                  value={selectedNode.data.config.tags || ''}
                  onChange={(e) => handleConfigChange('tags', e.target.value)}
                />
              </div>
            </div>
          )}
          {selectedNode.data.label === 'sg' && (
            <div>
              <div>
                <label>Security Group Name:</label>
                <input
                  type="text"
                  value={selectedNode.data.config.sgName || ''}
                  onChange={(e) => handleConfigChange('sgName', e.target.value)}
                />
              </div>
              <div>
                <label>Description:</label>
                <input
                  type="text"
                  value={selectedNode.data.config.description || ''}
                  onChange={(e) => handleConfigChange('description', e.target.value)}
                />
              </div>
              <div>
                <label>VPC ID:</label>
                <input
                  type="text"
                  value={selectedNode.data.config.vpcId || ''}
                  onChange={(e) => handleConfigChange('vpcId', e.target.value)}
                />
              </div>
            </div>
          )}
          <button onClick={handleDeleteNode} style={{ marginTop: '10px', background: 'red', color: 'white', border: 'none', padding: '5px 10px', cursor: 'pointer' }}>
            Delete Resource
          </button>
        </div>
      )}
      <button
        onClick={handleGenerateTerraformCode}
        style={{ position: 'absolute', top: '10px', left: '10px', background: 'blue', color: 'white', border: 'none', padding: '5px 10px', cursor: 'pointer' }}
      >
        Generate Terraform Code
      </button>
      <button
        onClick={exportTerraformCode}
        style={{ position: 'absolute', top: '50px', left: '10px', background: 'green', color: 'white', border: 'none', padding: '5px 10px', cursor: 'pointer' }}
      >
        Export Terraform Code
      </button>
      {terraformCode && (
        <div style={{ position: 'absolute', bottom: '10px', left: '10px', background: 'white', padding: '10px', border: '1px solid #ddd', width: '400px', maxHeight: '200px', overflowY: 'auto' }}>
          <h4>Generated Terraform Code</h4>
          <pre>{terraformCode}</pre>
        </div>
      )}
    </div>
  );
};

export default Canvas;