import React from 'react';
import Canvas from './Canvas';
import Sidebar from './Sidebar';
import './App.css';

function App() {
  return (
    <div className="App">
      <h1>InfraQ</h1>
      <div style={{ display: 'flex' }}>
        <Sidebar />
        <Canvas />
      </div>
    </div>
  );
}

export default App;