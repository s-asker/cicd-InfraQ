import React from 'react';

const Sidebar = () => {
  const onDragStart = (event, nodeType) => {
    event.dataTransfer.setData('application/reactflow', nodeType);
    event.dataTransfer.effectAllowed = 'move';
  };

  return (
    <aside style={{ width: '200px', padding: '10px', borderRight: '1px solid #ddd' }}>
      <h3>Cloud Resources</h3>
      <div
        style={{ padding: '10px', border: '1px solid #ddd', marginBottom: '10px', cursor: 'grab' }}
        onDragStart={(event) => onDragStart(event, 'vpc')}
        draggable
      >
        VPC
      </div>
      <div
        style={{ padding: '10px', border: '1px solid #ddd', marginBottom: '10px', cursor: 'grab' }}
        onDragStart={(event) => onDragStart(event, 'subnet')}
        draggable
      >
        Subnet
      </div>
      <div
        style={{ padding: '10px', border: '1px solid #ddd', marginBottom: '10px', cursor: 'grab' }}
        onDragStart={(event) => onDragStart(event, 'ec2')}
        draggable
      >
        EC2 Instance
      </div>
      <div
        style={{ padding: '10px', border: '1px solid #ddd', marginBottom: '10px', cursor: 'grab' }}
        onDragStart={(event) => onDragStart(event, 's3')}
        draggable
      >
        S3 Bucket
      </div>
      <div
        style={{ padding: '10px', border: '1px solid #ddd', marginBottom: '10px', cursor: 'grab' }}
        onDragStart={(event) => onDragStart(event, 'sg')}
        draggable
      >
        Security Group
      </div>
    </aside>
  );
};

export default Sidebar;