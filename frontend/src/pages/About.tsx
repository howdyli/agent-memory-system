import React from 'react';
import { Button, Card, Typography } from 'antd';
import { useNavigate } from 'react-router-dom';

const { Title, Paragraph } = Typography;

const About: React.FC = () => {
  const navigate = useNavigate();

  return (
    <div style={{ padding: '24px' }}>
      <Card>
        <Title level={2}>About Agent Memory System</Title>
        <Paragraph>
          This system provides intelligent memory management for AI agents,
          enabling persistent context and improved agent interactions.
        </Paragraph>
        <Paragraph>
          <strong>Features:</strong>
          <ul>
            <li>Vector-based memory storage</li>
            <li>FastAPI backend</li>
            <li>React + TypeScript frontend</li>
            <li>Real-time memory retrieval</li>
          </ul>
        </Paragraph>
        <Button onClick={() => navigate('/')}>
          Back to Home
        </Button>
      </Card>
    </div>
  );
};

export default About;
