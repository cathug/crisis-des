import simpy, random


# Globals
NUM_HELPSEEKERS_IN_SYSTEM = 0
NUM_HELPSEEKERS_IN_QUEUE = 0
NUM_HELPSEEKERS_IN_PRIORITY_QUEUE = [0, 0, 0]
MEAN_QUEUE_TIME_BEFORE_RENEGING = 7.0 # specify this as a float
LAMBDA_RENEGE = 1.0/MEAN_QUEUE_TIME_BEFORE_RENEGING # mean reneging counts
LUNCH_BREAK = 60 # 60 minute lunch break



class Counsellor:
    '''
        Counsellor Class with a limited number of counsellors to serve helpseekers
    '''

    def __init__(self, 
                 env,
                 counsellor_id,
                 service_duration,
                 lunch_duration=LUNCH_BREAK):
        self.env = env
        self.state = env.process(self.shift() )
        self.counsellor = f'Counsellor {counsellor_id}'
        self.service_duration = service_duration
        self.lunch_duration = lunch_duration

    #---------------------------------------------------------------------------
    
    def shift(self):
        while True:
            
            # start counselling for the first 6 hours
            print(f'{self.counsellor} starts shift at {self.env.now}')
            yield self.env.timeout(self.service_duration)


            # get lunch break
            yield self.env.process(self.lunch_break() )

            print(f'Counsellor returns from lunch at {self.env.now}')
            yield self.env.timeout(self.service_duration)
            
            print(f'{self.counsellor} shift ends at {self.env.now}')

    
    #---------------------------------------------------------------------------

    def lunch_break(self):
        '''
            Give counsellors a lunch break
            but interrupt when there is an urgent case incoming
        '''

        print(f'{self.counsellor} takes a lunch break at {self.env.now}')
        
        yield self.env.timeout(self.lunch_duration)
        print(f'{self.counsellor} lunch break ends at {self.env.now}')
        
        # try:
        #     yield self.env.timeout(self.lunch_duration)
        #     print(f'{self.counsellor} lunch break ends at {self.env.now}')
        # except simpy.Interrupt as i:
        #     print(f'{self.counsellor} lunch break interrupted at '
        #         f'{self.env.now} due to {i.cause}')


#-------------------------------------------------------end of Counsellors class

class Helpseeker:
    '''
        Helpseeker Class to be created according to an exponential distribution
    '''

    def __init__(self, env, helpseeker_id, openup_service, chat_duration):
        self.env = env
        self.chat_duration = chat_duration
        self.openup_service = openup_service
        self.user = f'Helpseeker {helpseeker_id}'


    def accept_TOS(self):
        

        print(f'{self.user} has accepted TOS.  Chat session created')

        with openup_service.request() as req:
            yield req

            print(f'{self.user} now being served')
            yield self.env.timeout(self.chat_duration)
            print(f'{self.user} chat session terminates')

    def renege(self):




def main():
    env = simpy.Environment()
    counsellor = Counsellor(env, 1, 8, 1)
    env.run(until=20)
    
    # helpseeker patience follows an exponential distribution
    patience = random.expovariate(LAMBDA_RENEGE)


if __name__ == '__main__':
    main()






# class QueueSimulationModel:
#     '''
#         The Queue Simulation Model
#     '''

#     def __init(self):
#         self.env = sp.Environment() # the execution environment
