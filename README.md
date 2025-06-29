IntentContinuum is a novel framework for intent-driven resource management in the compute continuum, enabling intelligent deployment and real-time adaptation of distributed IoT applications across edge and cloud environments.
This system leverages Large Language Models (LLMs)—specifically OpenAI GPT-4o—to monitor performance, analyze root causes of violations, and recommend reconfiguration actions, all while maintaining adherence to user-defined Service Level Objectives (SLOs) such as response time for image processing.

Key Features
> Intent-Aware Monitoring: Tracks application-level intents like latency or throughput and checks for violations in real time.
> LLM-Powered Root Cause Analysis: Uses GPT-4o to pinpoint issues (e.g., CPU bottlenecks, memory limits, network congestion).
> Automated Reconfiguration: Applies corrective actions suggested by the LLM to restore compliance with user-defined intents.
> Closed-Loop Feedback System: Continuously adapts to workload, topology, and environmental changes without human intervention.
> Edge–Cloud Optimization: Coordinates between edge and cloud resources to ensure optimal performance, low latency, and system reliability.

We provide an open-source prototype implementation of IntentContinuum, developed using widely adopted tools like Kubernetes and ONOS. The system is modular, extensible, and can be easily integrated into real-world IoT environments.
Our extensive experimental evaluations demonstrate that IntentContinuum:
> Outperforms traditional methods in maintaining SLOs under dynamic conditions
> Reduces manual intervention through automated diagnosis and action
> Enhances scalability and robustness of distributed IoT deployments

If you use this project in your research, please cite the associated paper (to be added once published).
