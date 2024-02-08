# urlup-be-infra

## Installation

Optionally use your preferred method of creating and activating a virtual environment. For example:

```bash
virtualenv --py 3.10 .venv
source .venv/bin/activate
```

Install local requirements:

```bash
pip install -r requirements.txt
```

To deploy the infrastructure, the [backend code](https://github.com/matt-fff/urlup-be) needs to be cloned, as well.

By default, the backend is expected at `../backend` relative to the root of this project.

```bash
git clone https://github.com/matt-fff/urlup-be ../backend
```

You can customize the location by passing the `lambdas_dir` configuration through Pulumi.

Example pulumi config `Pulumi.your-env.yaml`:

```yaml
config:
  lambdas_dir: /your/path/to/urlup-be/src/urlup_be/lambdas
  aws:region: us-east-1
  env: your-env
  api_domain:
    cert_domain: api.yoursite.com
    gateway_domain: api.yoursite.com
    zone_domain: yoursite.com
  redirect_domain:
    cert_domain: yrsite.com
    gateway_domain: yrsite.com
    zone_domain: yrsite.com
  table_name: urlup-your-env
  tags:
    app: urlup
    arbitray: whatever
  allowed_frontends:
    - https://yoursite.com
```

## Prerequisites

This project assumes that all of your domain zones and certificates are already allocated in AWS in us-east-1.

See https://github.com/matt-fff/urlup-domain-infra for allocating these domain resources.

You'll also need to install/configure [Pulumi](https://www.pulumi.com/docs/install/) and probably the [AWS CLI](https://aws.amazon.com/cli/).

## Running

Select your stack with `pulumi stack select` - you'll be given the option to create a new stack.
