import React from 'react';
import { Button, Card, Typography } from 'antd';
import { useNavigate } from 'react-router-dom';

const { Title, Paragraph } = Typography;

const Home: React.FC = () => {
  const navigate = useNavigate();

  return (
    <div style={{ padding: '24px' }}>
      <Card>
        <Title level={2}>Agent Memory System</Title>
        <Paragraph>
          Welcome to the Agent Memory System frontend. This application provides
          a modern interface for managing agent memories.
        </Paragraph>
        <Button type="primary" onClick={() => navigate('/about')}>
          Learn More
        </Button>
      </Card>
    </div>
  );
};

export default Home;
